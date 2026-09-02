"""Run live ingest against the grid and report what landed in the database.

Usage:
    python -m scripts.run_ingest --cameras CAM-04 CAM-01 --seconds 180
    python -m scripts.run_ingest --ai --seconds 600        # all AI-enabled cameras
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Alert, Sighting  # noqa: E402
from app.services.ingest import manager  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("sentinel.worker").setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameras", nargs="*", default=[])
    ap.add_argument("--ai", action="store_true", help="start all AI-enabled cameras")
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--no-anpr", action="store_true", help="detection only, skip OCR")
    args = ap.parse_args()

    init_db()

    if args.ai:
        started = manager.start_ai_cameras()
    else:
        started = [c for c in args.cameras if manager.start_camera(c, anpr=not args.no_anpr)]

    if not started:
        print("no cameras started")
        return 1

    print(f"ingesting from {', '.join(started)} for {args.seconds}s ...\n")
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(15)
        for row in manager.status():
            print(f"  {row['camera_id']}: {row['frames_processed']} frames, "
                  f"{row['sightings']} sightings, {row['plates_read']} plates, "
                  f"{row['reconnects']} reconnects, "
                  f"{row['discontinuities']} discontinuities", flush=True)

    manager.stop_all()
    time.sleep(4)

    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Sighting))
        with_plate = db.scalar(
            select(func.count()).select_from(Sighting).where(Sighting.plate.isnot(None))
        )
        alerts = db.scalar(select(func.count()).select_from(Alert))
        print(f"\ndatabase: {total} sightings ({with_plate} with a plate), {alerts} alerts")

        by_type = db.execute(
            select(Sighting.vehicle_type, func.count())
            .group_by(Sighting.vehicle_type)
            .order_by(func.count().desc())
        ).all()
        print("by vehicle type:", ", ".join(f"{t or '?'}={n}" for t, n in by_type))

        by_colour = db.execute(
            select(Sighting.vehicle_color, func.count())
            .group_by(Sighting.vehicle_color)
            .order_by(func.count().desc())
        ).all()
        print("by colour     :", ", ".join(f"{c or '?'}={n}" for c, n in by_colour))

        recent = list(db.scalars(
            select(Sighting).order_by(Sighting.id.desc()).limit(8)
        ))
        print("\nmost recent sightings:")
        for s in recent:
            ts = s.event_ts.strftime("%Y-%m-%d %H:%M:%S") if s.event_ts else "no overlay ts"
            print(f"  {s.camera_id}  {ts}  {s.vehicle_color or '-':<7} "
                  f"{s.vehicle_type or '-':<11} {s.direction or '-':<11} "
                  f"plate={s.plate or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
