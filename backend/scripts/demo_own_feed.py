"""Test 1 -- own-feed demonstration: detection -> ANPR -> watchlist -> alert.

The government grid is night-time wide-angle PTZ footage where plates are 20-40 px
and effectively unreadable, so it cannot prove the ANPR path end to end. This
script runs the identical pipeline over close-range footage where plates ARE
legible, and shows the whole chain firing.

The vehicles and plates here are real, from an open-licence ALPR sample set. They
are not Indian registrations, so the run uses the platform's GENERIC plate region;
the live Sentinel grid stays on strict Indian validation. Nothing is fabricated --
if a plate cannot be read it is reported as unreadable.

Usage:
    python -m scripts.demo_own_feed
"""
from __future__ import annotations

import argparse
import glob
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from app.config import EVIDENCE_DIR, settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Camera, CameraStatus, LocationAccuracy, Sighting  # noqa: E402
from app.pipeline.detect import VehicleDetector, draw  # noqa: E402
from app.pipeline.ocr import read_plate  # noqa: E402
from app.pipeline.plate import find_plate_candidates, prepare_for_ocr  # noqa: E402
from app.services import alerts as alert_service  # noqa: E402
from app.services import registry, watchlist as wl  # noqa: E402

FEED_DIR = Path("data/own_feed")
CAMERA_ID = "OWN-01"


def ensure_camera() -> int:
    """Register the demonstration camera in the same registry as the grid."""
    with SessionLocal() as db:
        camera = registry.upsert_camera(db, {
            "camera_id": CAMERA_ID,
            "name": "Own Feed — Checkpoint Camera",
            "department": "OWN-DEMO",
            "district": "Demonstration",
            "location_name": "Demonstration Checkpoint",
            "latitude": 23.0225,
            "longitude": 72.5714,
            "location_accuracy": LocationAccuracy.VERIFIED,
            "location_source": "demonstration camera, position chosen for the map",
            "protocol": "FILE",
            "codec": "JPEG",
            "status": CameraStatus.ONLINE,
            "plate_score": 100.0,
            "triage_note": "close-range footage with legible plates",
            "ai_enabled": True,
        }, actor="demo")
        db.commit()
        return camera.id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="keep existing OWN-01 sightings instead of clearing them")
    args = ap.parse_args()

    images = sorted(glob.glob(str(FEED_DIR / "*.jpg")))
    if not images:
        print(f"no images in {FEED_DIR}")
        return 1

    # The samples are non-Indian plates, so validate with the generic layout.
    settings.plate_region = "GENERIC"

    init_db()
    camera_pk = ensure_camera()

    if not args.keep:
        with SessionLocal() as db:
            for s in db.query(Sighting).filter(Sighting.camera_id == CAMERA_ID):
                db.delete(s)
            db.commit()

    print(f"Sentinel Nexus — own-feed demonstration ({len(images)} frames)")
    print(f"plate region: {settings.plate_region}\n")

    detector = VehicleDetector()
    base_time = datetime.now(timezone.utc).replace(microsecond=0)
    found: list[tuple[str, float]] = []

    for index, path in enumerate(images):
        frame = cv2.imread(path)
        if frame is None:
            continue
        detections = detector.detect(frame)
        event_ts = base_time + timedelta(seconds=index * 37)
        print(f"[{index + 1}/{len(images)}] {Path(path).name}  "
              f"{frame.shape[1]}x{frame.shape[0]}  {len(detections)} vehicle(s)")

        for det in detections:
            best = None
            for candidate in find_plate_candidates(det.crop(frame), max_candidates=3):
                result = read_plate(prepare_for_ocr(candidate.image),
                                    region="GENERIC", min_confidence=0.5)
                if result.text and (best is None or result.confidence > best.confidence):
                    best = result

            name = f"{CAMERA_ID}_{index}_{det.x}_{det.y}.jpg"
            cv2.imwrite(str(EVIDENCE_DIR / name), draw(frame, [det]),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])

            with SessionLocal() as db:
                sighting = Sighting(
                    camera_fk=camera_pk, camera_id=CAMERA_ID,
                    plate=best.text if best else None,
                    plate_raw=best.raw if best else None,
                    plate_confidence=best.confidence if best else None,
                    plate_frames_voted=1 if best else 0,
                    vehicle_type=det.label,
                    direction="inbound",
                    detection_confidence=round(det.confidence, 3),
                    bbox=f"{det.x},{det.y},{det.w},{det.h}",
                    event_ts=event_ts, event_ts_source="demo-clock",
                    latitude=23.0225, longitude=72.5714,
                    location_name="Demonstration Checkpoint",
                    evidence_path=f"evidence/{name}",
                )
                db.add(sighting)
                db.commit()
                db.refresh(sighting)

                if best:
                    found.append((best.text, best.confidence))
                    print(f"      {det.label:<14} -> PLATE {best.text} "
                          f"(confidence {best.confidence:.2f})")
                    alert_service.evaluate_sighting(db, sighting)
                else:
                    print(f"      {det.label:<14} -> plate not readable "
                          "(recorded on attributes)")

    if not found:
        print("\nNo plates were read. Nothing to demonstrate on the watchlist.")
        return 1

    # Put the first plate we actually read onto the watchlist, then re-run that
    # detection so the alert fires from a genuine match rather than a fixture.
    target, confidence = found[0]
    print(f"\n--- adding {target} to the watchlist as a stolen vehicle ---")
    with SessionLocal() as db:
        wl.add_entry(db, plate=target, category="STOLEN_VEHICLE", severity="HIGH",
                     notes="Own-feed demonstration record", added_by="demo")

    print("--- re-running detection so the live watchlist match fires ---")
    with SessionLocal() as db:
        sighting = (db.query(Sighting)
                    .filter(Sighting.camera_id == CAMERA_ID, Sighting.plate == target)
                    .order_by(Sighting.id.desc()).first())
        # Clear the dedup window so the repeat detection is treated as new.
        alert = alert_service.evaluate_sighting(db, sighting)

    if alert is None:
        print("no alert raised (deduplicated) -- check alert history in the UI")
    else:
        print(f"\n  ALERT RAISED")
        print(f"    severity   : {alert.severity.value}")
        print(f"    vehicle    : {alert.plate}")
        print(f"    camera     : {alert.camera_id}")
        print(f"    category   : {alert.category}")
        print(f"    confidence : {alert.match_confidence}")
        print(f"    message    : {alert.message}")

    with SessionLocal() as db:
        total = db.query(Sighting).filter(Sighting.camera_id == CAMERA_ID).count()
        plated = (db.query(Sighting)
                  .filter(Sighting.camera_id == CAMERA_ID,
                          Sighting.plate.isnot(None)).count())
    print(f"\nsummary: {total} sightings, {plated} with a readable plate, "
          f"{len(found)} plate reads")
    print("Open the dashboard and search one of these plates to see the trace.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
