"""Ingest orchestration: streams in, sightings and alerts out.

One worker thread per camera. Each thread pulls PTS-sampled frames, detects
vehicles, tracks them across frames, votes on the plate, and writes one sighting
per tracked vehicle rather than one per frame.

A sighting is recorded even when no plate could be read. Vehicle type, colour,
direction, time and place still support cross-camera correlation, and on this
footage that is frequently all there is. Nothing is invented to fill the gap.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import cv2
from sqlalchemy import select

from ..config import EVIDENCE_DIR, settings
from ..db import SessionLocal
from ..models import Camera, Sighting
from ..pipeline.detect import VehicleDetector, draw
from ..pipeline.overlay import read_overlay
from ..pipeline.plate import find_plate_candidates, prepare_for_ocr
from ..pipeline.ocr import read_plate
from ..pipeline.track import Track, VehicleTracker
from ..pipeline.worker import StreamWorker
from . import alerts as alert_service

log = logging.getLogger("sentinel.ingest")

#: Re-read the burned-in clock this often (frames). OCR is the expensive step and
#: the overlay clock advances predictably, so it is interpolated in between.
OVERLAY_EVERY = 25

#: Only attempt plate OCR on vehicles at least this wide. Measured: the chain
#: reads reliably down to ~60 px of plate, which needs a vehicle box well above
#: this; below it we save the CPU and record attributes only.
MIN_VEHICLE_WIDTH_FOR_ANPR = 90

#: Plate OCR is by far the most expensive step. Capping how many vehicles are
#: attempted per frame keeps a busy junction from starving the stream: CAM-04
#: managed 17 frames in 300 s unthrottled while CAM-01 managed 473.
MAX_ANPR_PER_FRAME = 2

#: Re-attempt OCR on the same track only every N frames -- consecutive frames of
#: one vehicle look nearly identical, so back-to-back reads add cost, not votes.
ANPR_RETRY_EVERY = 3

#: Stop voting once a track has this many accepted reads.
MAX_VOTES_PER_TRACK = 6

#: Fraction of frame height at top and bottom occupied by the burned-in overlay.
#: Vehicle boxes overlapping these bands are excluded from ANPR: the camera's own
#: timestamp and site caption otherwise OCR cleanly and masquerade as plates.
OVERLAY_BAND_RATIO = 0.10


class CameraIngest:
    """Processes a single camera end to end."""

    def __init__(self, camera: Camera, *, sample_interval_ms: float | None = None,
                 anpr: bool = True) -> None:
        self.camera_id = camera.camera_id
        self.camera_pk = camera.id
        self.url = camera.stream_url
        self.hls_url = camera.hls_url
        self.location_name = camera.location_name or ""
        self.latitude = camera.latitude
        self.longitude = camera.longitude
        self.anpr = anpr

        self.detector = VehicleDetector()
        self.tracker = VehicleTracker()
        self.worker = StreamWorker(
            self.url,
            camera_id=self.camera_id,
            fallback_url=self.hls_url,
            sample_interval_ms=sample_interval_ms or settings.sample_interval_ms,
        )

        self.sightings_written = 0
        self.frames_processed = 0
        self.plates_read = 0
        self.scenery_suppressed = 0
        self._overlay_ts: datetime | None = None
        self._overlay_pts: float | None = None

    # ----------------------------------------------------------------- timing

    def _event_time(self, frame, pts_ms: float, force: bool) -> datetime | None:
        """Authoritative event time from the burned-in overlay clock.

        Re-OCR'd periodically and interpolated with PTS in between, because the
        overlay is the only clock that reflects when the footage was recorded.
        """
        if force or self._overlay_ts is None:
            read = read_overlay(frame, want_site=False)
            if read.timestamp is not None:
                self._overlay_ts = read.timestamp.replace(tzinfo=timezone.utc)
                self._overlay_pts = pts_ms
                return self._overlay_ts

        if self._overlay_ts is not None and self._overlay_pts is not None:
            from datetime import timedelta

            return self._overlay_ts + timedelta(milliseconds=pts_ms - self._overlay_pts)
        return None

    # -------------------------------------------------------------- persistence

    def _write_sighting(self, track: Track) -> None:
        """Persist one finished track as a sighting, and evaluate the watchlist.

        Fixed scene structure is dropped rather than recorded. The detector runs
        on COCO weights, so a toll gantry, a booth cabin or a barrier post lands
        on a vehicle class often enough to be detected every frame; on a cluttered
        forecourt that is most of what gets detected. A sighting is a claim that a
        vehicle passed, and structure never passed anything.
        """
        if track.is_static_scenery:
            self.scenery_suppressed += 1
            log.debug("%s: track %d suppressed as scenery (%d hits, %.1f px drift)",
                      self.camera_id, track.track_id, track.hits, track.max_drift_px)
            return

        read, frames_voted = track.voter.consensus()
        event_ts = track.last_event_ts or track.first_event_ts

        evidence_path = None
        plate_crop_path = None
        if track.best_frame is not None:
            stamp = int(time.time() * 1000)
            name = f"{self.camera_id}_{track.track_id}_{stamp}.jpg"
            path = EVIDENCE_DIR / name
            cv2.imwrite(str(path), track.best_frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            evidence_path = f"evidence/{name}"

        with SessionLocal() as db:
            sighting = Sighting(
                camera_fk=self.camera_pk,
                camera_id=self.camera_id,
                plate=read.text or None,
                plate_raw=read.raw or None,
                plate_confidence=read.confidence or None,
                plate_frames_voted=frames_voted,
                vehicle_type=track.label,
                direction=track.direction or None,
                detection_confidence=round(track.best_confidence, 3) or None,
                bbox=f"{track.detection.x},{track.detection.y},"
                     f"{track.detection.w},{track.detection.h}",
                event_ts=event_ts,
                event_ts_source="overlay" if event_ts else "none",
                pts_ms=track.last_pts_ms,
                latitude=self.latitude,
                longitude=self.longitude,
                location_name=self.location_name,
                evidence_path=evidence_path,
                plate_crop_path=plate_crop_path,
            )
            db.add(sighting)
            db.commit()
            db.refresh(sighting)

            self.sightings_written += 1
            if read.text:
                self.plates_read += 1
                alert_service.evaluate_sighting(db, sighting)

    # -------------------------------------------------------------------- main

    def run(self) -> None:
        log.info("%s: ingest starting", self.camera_id)
        try:
            for event in self.worker.stream():
                frame = event.frame

                # The feed looped: the next frame is an unrelated moment, so close
                # every open track rather than inventing a journey across the cut.
                if event.discontinuity or event.reconnected:
                    for track in self.tracker.reset():
                        self._write_sighting(track)
                    self._overlay_ts = None

                force_overlay = (self.frames_processed % OVERLAY_EVERY) == 0
                event_ts = self._event_time(frame, event.pts_ms, force_overlay)

                detections = self.detector.detect(frame)
                _, finished = self.tracker.update(detections, event.pts_ms, event_ts)

                # Always keep the best view of every track for evidence.
                for track in self.tracker.tracks:
                    d = track.detection
                    if d.confidence > track.best_confidence:
                        track.best_confidence = d.confidence
                        track.best_frame = draw(frame, [d])

                # Plate work only on the largest, closest vehicles, and only on a
                # few per frame -- OCR is the pipeline's cost centre.
                if self.anpr:
                    height = frame.shape[0]
                    top_band = height * OVERLAY_BAND_RATIO
                    bottom_band = height * (1.0 - OVERLAY_BAND_RATIO)

                    candidates = sorted(
                        (t for t in self.tracker.tracks
                         if t.detection.w >= MIN_VEHICLE_WIDTH_FOR_ANPR
                         and t.voter.total_reads < MAX_VOTES_PER_TRACK
                         # Structure never yields an accepted read, so total_reads
                         # stays 0 and it would otherwise be re-OCR'd forever.
                         and not t.is_static_scenery
                         and (t.hits % ANPR_RETRY_EVERY) == 1
                         # Exclude boxes straying into the overlay bands.
                         and t.detection.y >= top_band
                         and (t.detection.y + t.detection.h) <= bottom_band),
                        key=lambda t: t.detection.area,
                        reverse=True,
                    )[:MAX_ANPR_PER_FRAME]

                    for track in candidates:
                        crop = track.detection.crop(frame)
                        for candidate in find_plate_candidates(crop, max_candidates=2):
                            result = read_plate(prepare_for_ocr(candidate.image))
                            if result.text:
                                track.voter.add(result)
                                break     # one accepted read per frame is enough

                for track in finished:
                    self._write_sighting(track)

                self.frames_processed += 1
        except Exception:
            log.exception("%s: ingest crashed", self.camera_id)
        finally:
            for track in self.tracker.flush():
                self._write_sighting(track)
            log.info("%s: ingest stopped (%d frames, %d sightings, %d plates, "
                     "%d scenery suppressed)",
                     self.camera_id, self.frames_processed,
                     self.sightings_written, self.plates_read,
                     self.scenery_suppressed)

    def stop(self) -> None:
        self.worker.stop()


class IngestManager:
    """Starts and stops per-camera ingest threads."""

    def __init__(self) -> None:
        self._threads: dict[str, threading.Thread] = {}
        self._ingests: dict[str, CameraIngest] = {}
        self._lock = threading.Lock()

    def start_camera(self, camera_id: str, *, anpr: bool = True) -> bool:
        with self._lock:
            if camera_id in self._threads and self._threads[camera_id].is_alive():
                return False
            with SessionLocal() as db:
                camera = db.scalar(select(Camera).where(Camera.camera_id == camera_id))
                if camera is None or not camera.stream_url:
                    return False
                ingest = CameraIngest(camera, anpr=anpr)

            thread = threading.Thread(target=ingest.run, name=f"ingest-{camera_id}",
                                      daemon=True)
            self._ingests[camera_id] = ingest
            self._threads[camera_id] = thread
            thread.start()
            return True

    def stop_camera(self, camera_id: str) -> bool:
        with self._lock:
            ingest = self._ingests.get(camera_id)
            if ingest is None:
                return False
            ingest.stop()
            return True

    def stop_all(self) -> None:
        with self._lock:
            for ingest in self._ingests.values():
                ingest.stop()

    def start_ai_cameras(self, limit: int | None = None) -> list[str]:
        """Start the cameras the triage marked worth processing."""
        with SessionLocal() as db:
            cameras = list(db.scalars(
                select(Camera)
                .where(Camera.ai_enabled.is_(True))
                .order_by(Camera.plate_score.desc())
            ))
        cap = limit or settings.max_concurrent_streams
        started = []
        for camera in cameras[:cap]:
            if self.start_camera(camera.camera_id):
                started.append(camera.camera_id)
        return started

    def status(self) -> list[dict]:
        return [
            {
                "camera_id": cid,
                "running": self._threads[cid].is_alive(),
                "frames_processed": ing.frames_processed,
                "sightings": ing.sightings_written,
                "plates_read": ing.plates_read,
                "scenery_suppressed": ing.scenery_suppressed,
                "reconnects": max(0, ing.worker.reconnects - 1),
                "discontinuities": ing.worker.discontinuities,
            }
            for cid, ing in self._ingests.items()
        ]


manager = IngestManager()
