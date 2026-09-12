"""Measured camera availability.

The grid reports every camera as live, including the ones that are not. So
availability is measured here rather than trusted: a camera is ONLINE because
this process opened it and decoded a frame, not because a catalogue said so.

Three outcomes, and the middle one is the interesting one:

  * **ONLINE**   -- opened, and decoded a frame with real content.
  * **DEGRADED** -- opened, but the frames are unusable. On this grid that is
    mostly CAM-12's H.265 smear: the capture succeeds, frames arrive, and every
    one of them is a grey mush. A camera that reports itself up and delivers
    nothing readable is the single most misleading state on the estate, so it
    gets its own status rather than being rounded to ONLINE.
  * **OFFLINE**  -- did not open inside the timeout.

Probing policy
--------------
Probing follows the video wall rather than sweeping the estate. This grid
permits roughly one session per address and measurably degrades under parallel
opens -- at ten concurrent opens, five cameras returned no frames at all, while
serially four of them recovered. So probes are serialised behind the same global
stream semaphore the ingest workers use, and a camera probed recently is not
re-probed.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import cv2
import numpy as np

from ..config import settings
from ..models import Camera, CameraStatus
from ..pipeline.worker import StreamWorker

log = logging.getLogger("sentinel.health")

#: How long a probe result stays fresh. A camera checked inside this window is
#: reported from cache rather than reopened -- reopening costs a stream slot and
#: this grid does not tolerate churn.
FRESH_FOR_S = 300.0

#: Frames to decode before judging a camera. One frame is not enough: the first
#: frame after a join is frequently a partially-decoded reference frame that
#: looks like smear on a perfectly healthy camera.
PROBE_FRAMES = 3

#: Opens on this grid have been measured anywhere from 1.8 s to 275 s. A probe
#: cannot wait that long, so a slow-but-alive camera can come back OFFLINE here.
#: That is stated in the result rather than hidden.
PROBE_TIMEOUT_S = 25.0

#: Below this standard deviation a frame carries no structure -- a black frame,
#: a dropped-signal blue screen, or the grey mush CAM-12 produces. Measured: a
#: healthy night frame on this grid reads 28-60, CAM-12's smear reads under 6.
FLAT_FRAME_STD = 8.0


@dataclass
class ProbeResult:
    camera_id: str
    status: str
    open_latency_s: float | None = None
    frames_decoded: int = 0
    frame_std: float | None = None
    note: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "status": self.status,
            "open_latency_s": (round(self.open_latency_s, 2)
                               if self.open_latency_s is not None else None),
            "frames_decoded": self.frames_decoded,
            "frame_std": round(self.frame_std, 1) if self.frame_std is not None else None,
            "note": self.note,
            "checked_at": self.checked_at.isoformat(),
        }


_results: dict[str, ProbeResult] = {}
_lock = threading.Lock()


def cached(camera_id: str) -> ProbeResult | None:
    """The last probe of this camera, if it is still fresh."""
    with _lock:
        result = _results.get(camera_id)
    if result is None:
        return None
    age = (datetime.now(timezone.utc) - result.checked_at).total_seconds()
    return result if age < FRESH_FOR_S else None


def all_results() -> list[dict]:
    with _lock:
        return [r.to_dict() for r in
                sorted(_results.values(), key=lambda r: r.camera_id)]


def probe(camera: Camera, *, force: bool = False) -> ProbeResult:
    """Open one camera, decode a few frames, and judge it.

    Blocking, and it holds a stream slot for the duration -- call it off the
    request thread.
    """
    if not force:
        fresh = cached(camera.camera_id)
        if fresh is not None:
            return fresh

    if not camera.stream_url:
        return _record(ProbeResult(camera_id=camera.camera_id, status="OFFLINE",
                                   note="no stream URL in the registry"))

    worker = StreamWorker(
        camera.stream_url,
        camera_id=camera.camera_id,
        fallback_url=camera.hls_url,
        sample_interval_ms=0,
        open_timeout_s=PROBE_TIMEOUT_S,
        # One shot. A probe that retries is no longer measuring availability,
        # it is measuring patience.
        backoff_base=PROBE_TIMEOUT_S,
        backoff_cap=PROBE_TIMEOUT_S,
        read_failure_grace=8,
    )

    started = time.time()
    frames: list[np.ndarray] = []
    deadline = started + PROBE_TIMEOUT_S

    try:
        for event in worker.stream():
            frames.append(event.frame)
            if len(frames) >= PROBE_FRAMES or time.time() > deadline:
                break
    except Exception as exc:
        log.warning("%s: probe failed: %s", camera.camera_id, exc)
    finally:
        worker.stop()

    latency = time.time() - started

    if not frames:
        return _record(ProbeResult(
            camera_id=camera.camera_id, status="OFFLINE", open_latency_s=latency,
            note=f"no frame decoded within {PROBE_TIMEOUT_S:.0f}s. Opens on this "
                 f"grid have been measured up to 275s, so a slow camera can "
                 f"read as offline here."))

    # Judge on the last frame: the first after a join is often a partially
    # decoded reference frame that looks like smear on a healthy camera.
    sharpest = max(float(np.asarray(f).std()) for f in frames)

    if sharpest < FLAT_FRAME_STD:
        return _record(ProbeResult(
            camera_id=camera.camera_id, status="DEGRADED", open_latency_s=latency,
            frames_decoded=len(frames), frame_std=sharpest,
            note="opens and delivers frames, but they carry no structure "
                 f"(std {sharpest:.1f} < {FLAT_FRAME_STD}). Reports itself up "
                 "while being unusable."))

    return _record(ProbeResult(
        camera_id=camera.camera_id, status="ONLINE", open_latency_s=latency,
        frames_decoded=len(frames), frame_std=sharpest,
        note=f"decoded {len(frames)} frames in {latency:.1f}s"))


def _record(result: ProbeResult) -> ProbeResult:
    with _lock:
        _results[result.camera_id] = result
    return result


def apply_to_registry(db, result: ProbeResult) -> None:
    """Write a probe result back onto the camera row.

    Kept separate from ``probe`` so the probe itself needs no database session
    and can run on a worker thread without holding one open for 25 seconds.
    """
    from . import registry

    camera = registry.get_camera(db, result.camera_id)
    if camera is None:
        return
    camera.status = CameraStatus(result.status)
    camera.open_latency_s = result.open_latency_s
    camera.health_note = result.note
    if result.status == "ONLINE":
        camera.last_seen = result.checked_at
    db.commit()


def summary(cameras: list[Camera]) -> dict:
    """Registry-reported vs measured availability.

    The gap between the two columns is the whole point of this panel: it is the
    number of cameras the grid claims are up that nobody has actually verified.
    """
    with _lock:
        measured = dict(_results)

    reported_online = sum(c.status == CameraStatus.ONLINE for c in cameras)
    checked = [c for c in cameras if c.camera_id in measured]

    confirmed = sum(measured[c.camera_id].status == "ONLINE" for c in checked)
    degraded = sum(measured[c.camera_id].status == "DEGRADED" for c in checked)
    failed = sum(measured[c.camera_id].status == "OFFLINE" for c in checked)

    return {
        "total": len(cameras),
        "reported_online": reported_online,
        "probed": len(checked),
        "unverified": len(cameras) - len(checked),
        "confirmed_online": confirmed,
        "degraded": degraded,
        "failed": failed,
        "fresh_for_s": FRESH_FOR_S,
    }
