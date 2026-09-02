"""Live stream worker.

Built to the field rules for this grid:

  * RTSP is forced over TCP. UDP fails across NAT and yields corrupt frames that
    look exactly like model bugs. HLS is the fallback when 8554 is blocked.
  * Timing comes from PTS only. CAP_PROP_FPS lies on this fleet (CAM-06 reports
    90000, CAM-30 reports 200), and arrival time lies too, because a buffered GOP
    replays faster than real time on connect.
  * Inter-frame gaps are normal and are never treated as a disconnect.
  * Reconnects use exponential backoff, 2 s -> 30 s cap. Never a tight loop.
  * Join-time decoder warnings ("Could not find ref with POC") are logged once,
    never fatal -- they self-correct at the first IDR.
  * Each feed loops. At the loop point the scene cuts and PTS jumps backwards;
    that is surfaced as a discontinuity so stateful consumers can reset.
  * Each client gets its own stream copy, so concurrency is capped and captures
    are closed the moment a worker is done with them.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

# Must be set before cv2 opens any capture.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp"          # never UDP
    "|stimeout;10000000"          # 10 s socket timeout (microseconds)
    "|probesize;500000"
    "|analyzeduration;1000000"
    "|max_delay;500000"
    "|reorder_queue_size;0"
    "|loglevel;error",            # decoder noise stays out of stdout
)
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
# libavcodec logs "error while decoding MB ..." straight to stderr on every
# corrupt macroblock. On this grid that is constant background noise, not a
# failure -- it clears at the first IDR. -8 is AV_LOG_QUIET.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import cv2  # noqa: E402

log = logging.getLogger("sentinel.worker")

#: Cap on simultaneously open captures. Each client gets its own stream copy, and
#: this grid measurably degrades under parallel opens: at 10 concurrent opens,
#: five cameras returned no frames at all; serially, four of them recovered.
_OPEN_SEMAPHORE = threading.Semaphore(int(os.getenv("SENTINEL_MAX_STREAMS", "4")))

#: A PTS that goes backwards by more than this means the feed looped.
LOOP_BACKWARD_MS = 1000.0
#: A forward PTS jump this large is a discontinuity too (feed restart / seek).
LOOP_FORWARD_MS = 60_000.0


def detect_discontinuity(previous_pts: float | None, pts: float) -> bool:
    """True when the PTS jump means the feed cut rather than simply advanced.

    Each feed loops; at the loop point PTS drops back to the start of the
    recording. A large forward jump means the same thing (restart or seek).
    Ordinary inter-frame gaps -- which are normal and frequent here, up to
    several seconds -- must NOT be reported as discontinuities.
    """
    if previous_pts is None:
        return False
    delta = pts - previous_pts
    return delta < -LOOP_BACKWARD_MS or delta > LOOP_FORWARD_MS


@dataclass(slots=True)
class FrameEvent:
    """One sampled frame plus the timing facts a consumer is allowed to trust."""

    frame: object                 # numpy.ndarray
    pts_ms: float                 # decoder presentation timestamp
    pts_delta_ms: float           # PTS gap since the previous delivered frame
    index: int                    # sequence number within this connection
    discontinuity: bool = False   # loop point or stream restart -- reset state
    reconnected: bool = False     # first frame after a reconnect


class StreamWorker:
    """Pulls frames from one camera, sampled by PTS, resilient to restarts.

    Usage::

        worker = StreamWorker(url)
        for event in worker.stream():
            ...
        worker.stop()
    """

    def __init__(
        self,
        url: str,
        *,
        camera_id: str = "",
        sample_interval_ms: float = 400.0,
        backoff_base: float = 2.0,
        backoff_cap: float = 30.0,
        open_timeout_s: float = 120.0,
        read_failure_grace: int = 30,
        fallback_url: str | None = None,
    ) -> None:
        self.url = url
        self.fallback_url = fallback_url          # HLS, when RTSP/8554 is blocked
        self.camera_id = camera_id or url
        self.sample_interval_ms = sample_interval_ms
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.open_timeout_s = open_timeout_s
        # Consecutive failed reads tolerated before declaring the feed gone.
        # Gaps between frames are normal on this grid and must not trigger a
        # reconnect, so this is deliberately generous.
        self.read_failure_grace = read_failure_grace

        self._stop = threading.Event()
        self._warned_decoder = False
        self.frames_delivered = 0
        self.reconnects = 0
        self.discontinuities = 0

    # ------------------------------------------------------------------ control

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------------- opening

    def _open(self, url: str) -> cv2.VideoCapture | None:
        """Open one capture, counted against the global concurrency cap."""
        acquired = _OPEN_SEMAPHORE.acquire(timeout=self.open_timeout_s)
        if not acquired:
            log.warning("%s: timed out waiting for a stream slot", self.camera_id)
            return None
        try:
            started = time.time()
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                _OPEN_SEMAPHORE.release()
                return None
            log.info("%s: opened in %.1fs", self.camera_id, time.time() - started)
            return cap
        except Exception as exc:
            log.warning("%s: open failed: %s", self.camera_id, exc)
            _OPEN_SEMAPHORE.release()
            return None

    def _close(self, cap: cv2.VideoCapture | None) -> None:
        """Release the capture and its concurrency slot. Always paired with _open."""
        if cap is None:
            return
        try:
            cap.release()
        except Exception:
            pass
        finally:
            _OPEN_SEMAPHORE.release()

    def _connect_with_backoff(self) -> cv2.VideoCapture | None:
        """Exponential backoff, 2 s -> 30 s cap. Falls back to HLS if configured."""
        attempt = 0
        while not self.stopped:
            url = self.url
            # Alternate to the fallback after repeated primary failures, in case
            # 8554 is blocked from this network rather than the camera being down.
            if self.fallback_url and attempt >= 2 and attempt % 2 == 0:
                url = self.fallback_url

            cap = self._open(url)
            if cap is not None:
                return cap

            delay = min(self.backoff_base * (2 ** attempt), self.backoff_cap)
            log.info("%s: reconnect attempt %d in %.0fs", self.camera_id, attempt + 1, delay)
            if self._stop.wait(delay):        # interruptible sleep, never a tight loop
                return None
            attempt += 1
        return None

    # -------------------------------------------------------------------- frames

    def stream(self) -> Iterator[FrameEvent]:
        """Yield sampled frames until stop() is called.

        Reconnects on its own; the caller never sees the seam except as
        ``reconnected`` and ``discontinuity`` flags on the next event.
        """
        cap: cv2.VideoCapture | None = None
        just_reconnected = True

        try:
            while not self.stopped:
                if cap is None:
                    cap = self._connect_with_backoff()
                    if cap is None:
                        return
                    self.reconnects += 1
                    just_reconnected = True

                last_pts: float | None = None
                last_emitted_pts: float | None = None
                index = 0
                read_failures = 0

                while not self.stopped:
                    # grab() advances the stream without converting the frame into
                    # a numpy array; retrieve() is only paid for frames we actually
                    # keep. At a 400 ms sample on a 25 fps feed that is ~1 conversion
                    # per 10 frames, which is what makes this viable on a low-end
                    # CPU with no GPU.
                    try:
                        ok = cap.grab()
                    except Exception as exc:
                        # A decode exception at join is not fatal; it clears at the
                        # first IDR. Log once, keep reading.
                        if not self._warned_decoder:
                            log.info("%s: decoder warning at join (%s) -- continuing",
                                     self.camera_id, exc)
                            self._warned_decoder = True
                        ok = False

                    frame = None
                    if not ok:
                        read_failures += 1
                        # Gaps are normal. Only give up after a sustained run of
                        # failures, then reconnect with backoff.
                        if read_failures >= self.read_failure_grace:
                            log.info("%s: stream ended after %d empty reads; reconnecting",
                                     self.camera_id, read_failures)
                            break
                        time.sleep(0.05)
                        continue

                    read_failures = 0
                    pts = cap.get(cv2.CAP_PROP_POS_MSEC)

                    # Some frames carry no usable PTS; fall back to the previous
                    # value so a zero never corrupts a delta.
                    if pts is None or pts <= 0:
                        pts = last_pts if last_pts is not None else 0.0

                    delta = 0.0 if last_pts is None else pts - last_pts

                    # Loop point: the feed restarts and PTS jumps backwards. Also
                    # catch an implausibly large forward jump.
                    discontinuity = detect_discontinuity(last_pts, pts)
                    if discontinuity:
                        self.discontinuities += 1
                        log.info("%s: scene discontinuity (PTS %.0f -> %.0f); "
                                 "resetting sampler", self.camera_id, last_pts, pts)
                        last_emitted_pts = None      # restart sampling cleanly

                    last_pts = pts

                    # Sample by PTS, not by arrival time -- a buffered GOP replays
                    # faster than real time right after connect.
                    if (
                        last_emitted_pts is not None
                        and not discontinuity
                        and (pts - last_emitted_pts) < self.sample_interval_ms
                    ):
                        continue        # skipped without ever decoding to a numpy array

                    # Only now pay for the colour conversion and buffer copy.
                    ok, frame = cap.retrieve()
                    if not ok or frame is None:
                        continue

                    emit_delta = 0.0 if last_emitted_pts is None else pts - last_emitted_pts
                    last_emitted_pts = pts
                    index += 1
                    self.frames_delivered += 1

                    yield FrameEvent(
                        frame=frame,
                        pts_ms=pts,
                        pts_delta_ms=emit_delta,
                        index=index,
                        discontinuity=discontinuity,
                        reconnected=just_reconnected,
                    )
                    just_reconnected = False

                # Fell out of the read loop: drop the capture and reconnect.
                self._close(cap)
                cap = None
        finally:
            self._close(cap)


def open_camera(url: str, **kwargs) -> StreamWorker:
    return StreamWorker(url, **kwargs)
