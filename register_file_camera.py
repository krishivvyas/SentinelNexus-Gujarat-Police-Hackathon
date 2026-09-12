"""Point a camera at a local video file, so the ANPR console has something to read.

Why this exists: the ITMS grid reads no registrations. Measured, not assumed --
71 OCR attempts across 8 cameras returned nothing, because those are night-time
wide-angle PTZ cameras where a plate is 20-40 px across. The chain itself is
fine; it reads a rendered plate at 0.99 and this estate's own checkpoint feed at
0.88-0.99. The estate is the problem, not the software.

So the honest way to demonstrate or evaluate ANPR here is to feed it footage
where a plate is actually resolvable: a checkpoint, a gate, a toll lane -- the
camera low and close, the vehicle near-frontal, the registration 100 px or wider.
``OWN-01`` was exactly that and its source file is no longer on disk, which is
why this script exists rather than a line in the README.

Usage::

    python register_file_camera.py OWN-01 "C:/footage/gate-cam.mp4"
    python register_file_camera.py GATE-02 ./lane2.mp4 --name "Gate 2 lane"

The file path is stored in ``stream_url`` unchanged. Nothing special happens to
it downstream -- ``StreamWorker`` hands whatever it is to ``cv2.VideoCapture``,
which opens a path as readily as an RTSP URL, and the rest of the pipeline
cannot tell the difference. That is also why there is no "file connector": there
was never anything for one to do.

What this does NOT do is start ingest. Registering a source and deciding to
spend a stream slot on it are separate decisions, and this script makes only the
first. Start it from the command centre, or::

    curl -X POST localhost:8000/api/ingest/start -H 'Content-Type: application/json' \
         -d '["OWN-01"]'
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

import cv2
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Camera, CameraStatus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("camera_id", help="existing camera to repoint, or a new id to create")
    ap.add_argument("video", help="path to a local video file")
    ap.add_argument("--name", default=None, help="display name (new cameras only)")
    ap.add_argument("--location", default=None, help="location name shown on cards")
    ap.add_argument("--no-anpr", action="store_true", help="register without plate reading")
    args = ap.parse_args()

    path = Path(args.video).expanduser().resolve()
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    # Open it here rather than letting the ingest worker discover the problem
    # twenty minutes later in a log nobody is reading. A file OpenCV cannot
    # decode is a codec problem, and it is much cheaper to say so now.
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    cap.release()
    if not ok or frame is None:
        print(f"error: OpenCV opened {path.name} but could not decode a frame.\n"
              "       The container or codec is unsupported by this build.", file=sys.stderr)
        return 2

    h, w = frame.shape[:2]
    print(f"{path.name}: {w}x{h}, {frames} frames @ {fps:.1f} fps")
    # A warning, not a refusal. Plate width depends on where the camera sits, not
    # on the frame size, and this is a heuristic -- but 848x480 wide-angle
    # footage has never produced a reading on this platform, and saying so now
    # saves somebody concluding the chain is broken.
    if w < 1000:
        print(f"  note: {w} px wide. Registrations resolve reliably from about "
              "100 px across;\n        on a wide shot at this width they will "
              "not. Expect attribute\n        sightings but no plates.")

    with SessionLocal() as db:
        camera = db.scalar(select(Camera).where(Camera.camera_id == args.camera_id))
        created = camera is None
        if created:
            camera = Camera(
                camera_id=args.camera_id,
                name=args.name or args.camera_id,
                department="OWN-DEMO",
                status=CameraStatus.ONLINE,
            )
            db.add(camera)

        camera.stream_url = str(path)
        camera.ai_enabled = not args.no_anpr
        if args.name:
            camera.name = args.name
        if args.location:
            camera.location_name = args.location
        db.commit()

    verb = "registered" if created else "repointed"
    print(f"\n{verb} {args.camera_id} -> {path}")
    print(f"  plate reading: {'off' if args.no_anpr else 'on'}")
    print("\nStart it, then watch /plates:")
    print(f'  curl -X POST localhost:8000/api/ingest/start -H "Content-Type: application/json" '
          f"-d '[\"{args.camera_id}\"]'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
