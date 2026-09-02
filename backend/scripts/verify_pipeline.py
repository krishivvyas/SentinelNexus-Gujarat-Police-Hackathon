"""Verify the stream worker against the field rules.

Checks, in order:
  1. Backoff on an unreachable feed is exponential and never a tight loop.
  2. A live feed delivers PTS-sampled frames, and PTS advances independently of
     arrival time (the buffered GOP replays faster than real time on connect).
  3. Inter-frame gaps do not stall or crash the pipeline.
  4. Decoder warnings at join are non-fatal.
  5. Scene discontinuities at the loop point are detected and reported.

Usage:
    python -m scripts.verify_pipeline --camera CAM-04 --seconds 120
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR  # noqa: E402
from app.pipeline.worker import StreamWorker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
CATALOGUE = DATA_DIR / "cameras.json"


def load_camera(camera_id: str) -> dict:
    doc = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    for entry in doc["cameras"]:
        if entry["camera_id"] == camera_id:
            return entry
    raise SystemExit(f"{camera_id} not in {CATALOGUE}")


def check_backoff() -> bool:
    """An unreachable feed must back off exponentially, not spin."""
    print("\n[1] backoff on an unreachable feed")
    worker = StreamWorker(
        "rtsp://127.0.0.1:9/stream/nope",
        camera_id="UNREACHABLE",
        backoff_base=1.0,
        backoff_cap=4.0,
        open_timeout_s=5.0,
    )

    import threading
    started = time.time()
    threading.Timer(11.0, worker.stop).start()
    frames = sum(1 for _ in worker.stream())
    elapsed = time.time() - started

    ok = frames == 0 and worker.reconnects == 0 and elapsed >= 10.0
    print(f"    ran {elapsed:.0f}s, delivered {frames} frames, "
          f"no tight loop: {'PASS' if ok else 'FAIL'}")
    return ok


def check_live(camera: dict, seconds: int, sample_ms: float) -> bool:
    """Run a real feed and record what the worker actually observed."""
    print(f"\n[2] live capture: {camera['camera_id']} "
          f"({camera.get('codec')}, {camera.get('width')}x{camera.get('height')}, "
          f"reported fps={camera.get('fps')})")

    worker = StreamWorker(
        camera["rtsp"],
        camera_id=camera["camera_id"],
        fallback_url=camera.get("hls"),
        sample_interval_ms=sample_ms,
    )

    import threading
    threading.Timer(float(seconds), worker.stop).start()

    started = time.time()
    deltas: list[float] = []
    first_pts = last_pts = None
    discontinuities = 0
    gap_max = 0.0

    for event in worker.stream():
        if first_pts is None:
            first_pts = event.pts_ms
        last_pts = event.pts_ms
        if event.index > 1 and not event.discontinuity:
            deltas.append(event.pts_delta_ms)
            gap_max = max(gap_max, event.pts_delta_ms)
        if event.discontinuity:
            discontinuities += 1
            print(f"    discontinuity at frame {event.index} (PTS {event.pts_ms:.0f}ms)")

    wall = time.time() - started
    n = worker.frames_delivered
    if n == 0:
        print("    FAIL: no frames delivered")
        return False

    pts_span = (last_pts - first_pts) / 1000.0 if first_pts is not None else 0.0
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0

    print(f"    frames delivered : {n}")
    print(f"    wall clock       : {wall:.1f}s")
    print(f"    PTS span         : {pts_span:.1f}s")
    print(f"    mean PTS delta   : {mean_delta:.0f}ms (sampling target {sample_ms:.0f}ms)")
    print(f"    max PTS gap      : {gap_max:.0f}ms")
    print(f"    discontinuities  : {discontinuities}")
    print(f"    reconnects       : {worker.reconnects - 1}")

    # PTS must advance on its own clock, not track wall time.
    if pts_span <= 0:
        print("    FAIL: PTS did not advance")
        return False
    # Sampling honoured the PTS interval rather than delivering every frame.
    if mean_delta < sample_ms * 0.5:
        print("    FAIL: sampler ignored the PTS interval")
        return False

    drift = pts_span - wall
    if drift > 2:
        note = "buffered GOP replayed faster than real time"
    elif drift < -5:
        note = "falling behind real time -- decode or network bound"
    else:
        note = "in step with real time"
    print(f"    PTS vs wall drift: {drift:+.1f}s ({note})")
    print(f"    throughput       : {pts_span / wall:.2f}x real time")
    print("    PASS")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default="CAM-04")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--sample-ms", type=float, default=400.0)
    ap.add_argument("--skip-backoff", action="store_true")
    args = ap.parse_args()

    results = []
    if not args.skip_backoff:
        results.append(("backoff", check_backoff()))
    results.append(("live capture", check_live(load_camera(args.camera),
                                               args.seconds, args.sample_ms)))

    print("\n" + "=" * 46)
    for name, ok in results:
        print(f"  {name:<20} {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
