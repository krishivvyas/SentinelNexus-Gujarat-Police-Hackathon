"""One-off: recover plate crops for readings taken before crops were saved.

`plate_crop_path` was in the model from the start but always written as None, so
every reading predating that fix has a vehicle frame and no picture of the plate
it claims to have read. The information is not lost -- the evidence frame and the
vehicle bbox are both on the row -- so the crop can be recovered rather than the
rows left half-evidenced.

The rule: re-localise plate candidates inside the stored bbox, OCR each, and keep
only a candidate whose reading **matches the plate already on the row**. A crop
that reads as something else is not this row's plate, and guessing "probably the
widest one" would attach a picture to a claim it does not support. Rows where
nothing matches are left alone with their crop still null, which the console
renders as "no crop" rather than inventing one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

import cv2
from sqlalchemy import select

from app.config import DATA_DIR
from app.db import SessionLocal
from app.models import Sighting
from app.pipeline.ocr import read_plate
from app.pipeline.plate import find_plate_candidates, prepare_for_ocr

EVIDENCE_DIR = DATA_DIR / "evidence"


def main() -> int:
    recovered = failed = skipped = 0
    with SessionLocal() as db:
        rows = list(db.scalars(
            select(Sighting).where(
                Sighting.plate.isnot(None), Sighting.plate != "",
                Sighting.plate_crop_path.is_(None))))
        print(f"{len(rows)} reading(s) without a crop")

        for row in rows:
            if not row.evidence_path or not row.bbox:
                print(f"  #{row.id} {row.plate}: no evidence frame or bbox -- skipped")
                skipped += 1
                continue
            path = DATA_DIR / row.evidence_path
            frame = cv2.imread(str(path)) if path.exists() else None
            if frame is None:
                print(f"  #{row.id} {row.plate}: evidence frame missing -- skipped")
                skipped += 1
                continue

            x, y, w, h = (int(v) for v in row.bbox.split(","))
            H, W = frame.shape[:2]
            x, y = max(0, x), max(0, y)
            crop = frame[y:min(y + h, H), x:min(x + w, W)]
            if crop.size == 0:
                print(f"  #{row.id} {row.plate}: bbox outside the frame -- skipped")
                skipped += 1
                continue

            best = None
            for cand in find_plate_candidates(crop, max_candidates=4):
                if cand.image is None or cand.image.size == 0:
                    continue
                result = read_plate(prepare_for_ocr(cand.image))
                if result.text == row.plate:
                    best = cand
                    break

            if best is None:
                print(f"  #{row.id} {row.plate}: no candidate re-read as {row.plate} -- left null")
                failed += 1
                continue

            name = f"plate_{row.camera_id}_backfill_{row.id}.jpg"
            cv2.imwrite(str(EVIDENCE_DIR / name), best.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
            row.plate_crop_path = f"evidence/{name}"
            recovered += 1
            print(f"  #{row.id} {row.plate}: recovered -> {name}")

        db.commit()

    print(f"\nrecovered={recovered} unmatched={failed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
