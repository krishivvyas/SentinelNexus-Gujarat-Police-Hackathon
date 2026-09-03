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
from ..pipeline.detect import VehicleDetector, draw
from ..pipeline.worker import StreamWorker

log = logging.getLogger("sentinel.live")

#: Preview frames are downscaled before encoding. The browser shows them a few
#: hundred pixels wide, so sending 1080p JPEGs would waste both CPU and bandwidth.
PREVIEW_WIDTH = 960

#: JPEG quality for the preview. Detection runs on the full frame regardless.
JPEG_QUALITY = 72

#: Hard ceiling on a single viewer's session, so a forgotten browser tab cannot
#: hold a camera slot open forever.
MAX_SESSION_SECONDS = 900

BOUNDARY = "sentinelframe"


def _encode(frame, width: int = PREVIEW_WIDTH) -> bytes | None:
    height, original_width = frame.shape[:2]
    if original_width > width:
        scale = width / original_width
        frame = cv2.resize(frame, (width, int(height * scale)),
                           interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", frame,
                              [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buffer.tobytes() if ok else None


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
        # A tiny sample interval: this loop is meant to consume everything.
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

    detector = VehicleDetector() if detect else None
    reader = _LatestFrame(url, camera_id=camera_id, fallback_url=fallback_url)
    reader.start()

    started = time.time()
    delivered = 0
    last_seq = -1
    waited = 0.0

    try:
        while True:
            if time.time() - started > MAX_SESSION_SECONDS:
                yield _part(_status_frame(
                    "Preview session ended after 15 minutes.\nReload to resume."))
                break

            frame, pts, seq = reader.latest()
            if frame is None or seq == last_seq:
                time.sleep(0.05)
                waited += 0.05
                # Reassure the viewer while a slow camera is still opening.
                if frame is None and waited > 12.0:
                    waited = 0.0
                    yield _part(_status_frame(
                        f"Still connecting to {camera_id}...\n"
                        "This camera is slow to open; the stream will appear."))
                continue

            last_seq = seq
            waited = 0.0

            if detector is not None:
                detections = detector.detect(frame)
                frame = draw(frame, detections)
                label = (f"{camera_id}  {len(detections)} vehicle(s)"
                         if detections else f"{camera_id}  no vehicles")
            else:
                label = camera_id

            # Stamp the decoder clock so the viewer can see the feed advancing.
            cv2.putText(frame, f"{label}   PTS {pts / 1000:.1f}s",
                        (12, frame.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (60, 255, 120), 2, cv2.LINE_AA)

            payload = _encode(frame)
            if payload is None:
                continue
            delivered += 1
            yield _part(payload)
    except (GeneratorExit, ConnectionResetError):
        # Client closed the tab. Expected, not an error.
        pass
    except Exception:
        log.exception("%s: live preview failed", camera_id)
        yield _part(_status_frame("Preview failed. See server log."))
    finally:
        reader.stop()
        log.info("%s: live preview closed after %d frames", camera_id, delivered)


def media_type() -> str:
    return f"multipart/x-mixed-replace; boundary={BOUNDARY}"
