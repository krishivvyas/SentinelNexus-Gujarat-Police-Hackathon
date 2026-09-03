"""Live camera preview with detection drawn on.

Streams annotated frames to the browser as MJPEG
(``multipart/x-mixed-replace``). The browser renders that in a plain ``<img>``
tag -- no WebRTC, no HLS transcoding, no player library, no extra dependency.
It is the cheapest way to put a live view on screen, and on this hardware the
limiting factor is detection latency rather than transport anyway.

The point of this view is verification: an operator can watch a real camera and
see for themselves what the detector is and is not finding, rather than taking
the sighting count on trust.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator

import cv2

from ..config import settings
from ..pipeline.detect import VehicleDetector
from ..pipeline.worker import StreamWorker

log = logging.getLogger("sentinel.live")

#: Preview frames are downscaled before encoding. The browser shows them a few
#: hundred pixels wide, so sending 1080p JPEGs would waste both CPU and bandwidth.
PREVIEW_WIDTH = 960

#: JPEG quality for the preview. Detection runs on the full frame regardless.
JPEG_QUALITY = 72

#: Ceiling on the preview frame rate, so a fast camera cannot flood the browser.
#: It is only a cap: measured decode throughput on a 1080p H.264 stream here is
#: about 3.6 frames/s, so this is rarely the binding constraint. Video is still
#: emitted independently of detection, which runs in its own thread.
TARGET_FPS = 10

#: Hard ceiling on a single viewer's session, so a forgotten browser tab cannot
#: hold a camera slot open forever.
MAX_SESSION_SECONDS = 900

BOUNDARY = "sentinelframe"


def _to_preview(frame, width: int = PREVIEW_WIDTH):
    """Downscale to preview size, returning the frame and the scale applied."""
    height, original_width = frame.shape[:2]
    if original_width <= width:
        return frame, 1.0
    scale = width / original_width
    return cv2.resize(frame, (width, int(height * scale)),
                      interpolation=cv2.INTER_AREA), scale


def _encode(frame) -> bytes | None:
    ok, buffer = cv2.imencode(".jpg", frame,
                              [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buffer.tobytes() if ok else None


def annotate(preview, detections, scale: float):
    """Draw detections onto the already-downscaled preview frame.

    Annotating before the downscale was the original mistake: a label drawn at
    1920 px wide and then shrunk to 960 loses half its height and becomes
    unreadable. Drawing here means the text is rendered at the size it will
    actually be viewed, so it stays crisp.
    """
    for d in detections:
        x, y = int(d.x * scale), int(d.y * scale)
        w, h = int(d.w * scale), int(d.h * scale)

        cv2.rectangle(preview, (x, y), (x + w, y + h), (60, 230, 60), 2)

        text = f"{d.colour} {d.label}".strip() if d.colour else d.label
        text = f"{text} {d.confidence:.0%}"
        (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)

        # Keep the label inside the frame, and put it below the box if there is
        # no room above.
        ly = y - 6
        if ly - th - base < 0:
            ly = y + h + th + 6
        lx = max(0, min(x, preview.shape[1] - tw - 8))

        # Solid backing plate: green-on-traffic-scene is unreadable without one.
        cv2.rectangle(preview, (lx, ly - th - base - 3), (lx + tw + 8, ly + 2),
                      (20, 90, 20), -1)
        cv2.putText(preview, text, (lx + 4, ly - base + 1),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 255, 235), 1, cv2.LINE_AA)
    return preview


def _part(payload: bytes) -> bytes:
    return (f"--{BOUNDARY}\r\n"
            f"Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n").encode() + payload + b"\r\n"


def _status_frame(text: str, width: int = 960, height: int = 540) -> bytes:
    """A placeholder frame, so the viewer sees why nothing is arriving."""
    import numpy as np

    canvas = np.full((height, width, 3), 24, dtype=np.uint8)
    for i, line in enumerate(text.split("\n")):
        cv2.putText(canvas, line, (28, 60 + i * 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (200, 215, 230), 1, cv2.LINE_AA)
    ok, buffer = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buffer.tobytes() if ok else b""


class _LatestFrame:
    """Drains a camera as fast as it arrives, keeping only the newest frame.

    This decoupling is essential, not an optimisation. Detection costs ~230 ms
    while frames arrive every 40 ms, so a consumer that decodes and detects in
    the same loop falls steadily behind. The decoder's reference chain then
    breaks and the picture degrades into heavy smearing -- which looks exactly
    like a broken camera, but is self-inflicted.

    Reading continuously in a thread and discarding stale frames keeps the
    decoder healthy; the viewer simply sees the most recent frame each time.
    """

    def __init__(self, url: str, *, camera_id: str, fallback_url: str | None) -> None:
        # Zero sample interval: emit every frame the decoder manages to produce.
        #
        # This is counter-intuitive but measured. Decoding 1080p H.264 through
        # OpenCV on this CPU yields only ~3.6 frames/s, and PTS advances ~350 ms
        # per decoded frame because the rest are dropped upstream. Asking for a
        # sample interval on top of that discards frames we already paid full
        # price for, so throughput falls rather than rises:
        #
        #     sample=0 ms   -> 3.6 frames/s      <- best
        #     sample=83 ms  -> 1.2 frames/s
        #     sample=400 ms -> 0.6 frames/s
        #
        # The preview is therefore decoder-bound, not policy-bound.
        self.worker = StreamWorker(url, camera_id=camera_id,
                                   fallback_url=fallback_url,
                                   sample_interval_ms=0)
        self.camera_id = camera_id
        self._lock = threading.Lock()
        self._frame = None
        self._pts = 0.0
        self._seq = 0
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"live-{camera_id}")

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            for event in self.worker.stream():
                with self._lock:
                    self._frame = event.frame
                    self._pts = event.pts_ms
                    self._seq += 1
        except Exception:
            log.exception("%s: live reader stopped", self.camera_id)

    def latest(self) -> tuple[object, float, int]:
        with self._lock:
            return self._frame, self._pts, self._seq

    def stop(self) -> None:
        self.worker.stop()


def stream_camera(url: str, *, camera_id: str = "", detect: bool = True,
                  fallback_url: str | None = None,
                  sample_interval_ms: float | None = None) -> Iterator[bytes]:
    """Yield MJPEG parts for one camera until the client disconnects.

    Detection costs roughly 230 ms per frame on a CPU-only host, so with it
    enabled the preview updates a few times a second. The stream itself is
    still drained at full rate in the background so the picture stays clean.
    """
    # Opening a stream on this grid takes anywhere from 2 s to several minutes,
    # so tell the viewer what is happening rather than showing a broken image.
    yield _part(_status_frame(
        f"Connecting to {camera_id or 'camera'}...\n"
        "Streams on this grid take 10-90s to open."))

    reader = _LatestFrame(url, camera_id=camera_id, fallback_url=fallback_url)
    reader.start()

    # Detection runs in its own thread on whatever frame is current, and the
    # video loop draws the most recent result. Without this the preview could
    # only ever be as fast as the detector -- about 1.5 fps -- which reads as
    # constant buffering. Boxes now lag the picture by one detection interval
    # instead, which is far less distracting than stuttering video.
    latest_detections: list = []
    detect_stop = threading.Event()

    def _detect_loop() -> None:
        detector = VehicleDetector()
        nonlocal latest_detections
        seen = -1
        while not detect_stop.is_set():
            frame, _, seq = reader.latest()
            if frame is None or seq == seen:
                time.sleep(0.03)
                continue
            seen = seq
            try:
                latest_detections = detector.detect(frame)
            except Exception:
                log.exception("%s: detection failed", camera_id)
                time.sleep(0.5)

    detect_thread = None
    if detect:
        detect_thread = threading.Thread(target=_detect_loop, daemon=True,
                                         name=f"live-detect-{camera_id}")
        detect_thread.start()

    started = time.time()
    delivered = 0
    last_seq = -1
    waited = 0.0
    frame_interval = 1.0 / TARGET_FPS

    try:
        while True:
            loop_started = time.time()
            if loop_started - started > MAX_SESSION_SECONDS:
                yield _part(_status_frame(
                    "Preview session ended after 15 minutes.\nReload to resume."))
                break

            frame, pts, seq = reader.latest()
            if frame is None or seq == last_seq:
                time.sleep(0.02)
                waited += 0.02
                # Reassure the viewer while a slow camera is still opening.
                if frame is None and waited > 12.0:
                    waited = 0.0
                    yield _part(_status_frame(
                        f"Still connecting to {camera_id}...\n"
                        "This camera is slow to open; the stream will appear."))
                continue

            last_seq = seq
            waited = 0.0

            preview, scale = _to_preview(frame)
            preview = preview.copy()        # never annotate the reader's buffer

            detections = latest_detections if detect else []
            if detect:
                annotate(preview, detections, scale)
                label = (f"{camera_id}   {len(detections)} vehicle(s)"
                         if detections else f"{camera_id}   no vehicles")
            else:
                label = camera_id

            # Status strip on a dark plate so it stays readable over any scene.
            strip = f"{label}   PTS {pts / 1000:.1f}s"
            (sw, sh), sb = cv2.getTextSize(strip, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            bottom = preview.shape[0]
            cv2.rectangle(preview, (0, bottom - sh - sb - 12),
                          (sw + 20, bottom), (18, 22, 28), -1)
            cv2.putText(preview, strip, (10, bottom - sb - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (90, 240, 130), 1, cv2.LINE_AA)

            payload = _encode(preview)
            if payload is None:
                continue
            delivered += 1
            yield _part(payload)

            # Pace the output so a fast camera cannot flood the browser.
            spare = frame_interval - (time.time() - loop_started)
            if spare > 0:
                time.sleep(spare)
    except (GeneratorExit, ConnectionResetError):
        # Client closed the tab. Expected, not an error.
        pass
    except Exception:
        log.exception("%s: live preview failed", camera_id)
        yield _part(_status_frame("Preview failed. See server log."))
    finally:
        detect_stop.set()
        reader.stop()
        log.info("%s: live preview closed after %d frames", camera_id, delivered)


def media_type() -> str:
    return f"multipart/x-mixed-replace; boundary={BOUNDARY}"
