"""Day-1 camera survey and triage.

Walks the whole Sentinel fleet, records what each camera actually is (codec,
resolution, fps, open latency, decode health), saves a thumbnail, and scores each
camera on how usable it looks for plate reading.

That score is what decides where the CPU-bound AI workers spend their time. On a
machine with no GPU we cannot run ANPR on 30 x 1080p streams, so choosing the right
6-8 cameras is an engineering result, not a shortcut.

Usage:
    python -m scripts.survey_cameras --frames 25 --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR  # noqa: E402
from app.connectors.base import CameraDescriptor  # noqa: E402
from app.connectors.rtsp import RTSPConnector  # noqa: E402

THUMB_DIR = DATA_DIR / "thumbnails"
THUMB_DIR.mkdir(parents=True, exist_ok=True)
OUT_JSON = DATA_DIR / "camera_survey.json"


def frame_metrics(frame: np.ndarray) -> dict[str, float]:
    """Cheap, objective image-quality measures used for triage."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    luminance = float(gray.mean())
    contrast = float(gray.std())

    edges = cv2.Canny(gray, 60, 160)
    edge_density = float((edges > 0).mean())

    # Blown-out highlights (headlight bloom) and crushed blacks both destroy plates.
    blown = float((gray >= 250).mean())
    crushed = float((gray <= 8).mean())

    # Road scenes carry most of their vehicle detail in the lower half of the frame.
    lower = gray[h // 2:, :]
    lower_detail = float(cv2.Laplacian(lower, cv2.CV_64F).var())

    return {
        "sharpness": round(sharpness, 2),
        "luminance": round(luminance, 2),
        "contrast": round(contrast, 2),
        "edge_density": round(edge_density, 4),
        "blown_highlight_ratio": round(blown, 4),
        "crushed_black_ratio": round(crushed, 4),
        "lower_half_detail": round(lower_detail, 2),
        "width": w,
        "height": h,
    }


def corruption_ratio(frames: list[np.ndarray]) -> float:
    """Detect the horizontal smearing seen on the H.265 cameras.

    A cleanly decoded frame has vertical structure; a smeared one collapses into
    long horizontal streaks, so its vertical gradient energy drops far below its
    horizontal gradient energy.
    """
    if not frames:
        return 1.0
    bad = 0
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = float(np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)).mean())
        gy = float(np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)).mean())
        if gy > 0 and (gx / max(gy, 1e-6)) > 2.2:
            bad += 1
    return round(bad / len(frames), 3)


def plate_score(m: dict[str, float], corruption: float, frames_read: int) -> tuple[float, str]:
    """Score 0-100 for plate-reading suitability, with the reasoning recorded."""
    if frames_read == 0:
        return 0.0, "no frames decoded"
    if corruption > 0.5:
        return 5.0, f"decode corruption on {corruption:.0%} of sampled frames"

    notes: list[str] = []
    score = 50.0

    # Sharpness is the strongest single predictor of a readable plate.
    if m["lower_half_detail"] > 400:
        score += 20; notes.append("high near-field detail")
    elif m["lower_half_detail"] > 150:
        score += 10; notes.append("moderate near-field detail")
    else:
        score -= 15; notes.append("low near-field detail")

    # Near-total darkness kills OCR outright.
    if m["luminance"] < 25:
        score -= 30; notes.append(f"very dark scene (mean luma {m['luminance']:.0f})")
    elif m["luminance"] < 50:
        score -= 12; notes.append("dark scene")

    if m["contrast"] < 25:
        score -= 10; notes.append("low contrast")

    if m["blown_highlight_ratio"] > 0.02:
        score -= 10; notes.append("headlight bloom")
    if m["crushed_black_ratio"] > 0.45:
        score -= 15; notes.append("large crushed-black region")

    if m["edge_density"] > 0.06:
        score += 8; notes.append("dense scene structure")

    if corruption > 0.15:
        score -= 15; notes.append(f"intermittent decode artefacts ({corruption:.0%})")

    return round(max(0.0, min(100.0, score)), 1), "; ".join(notes)


def survey_one(n: int, connector: RTSPConnector, frames_to_grab: int) -> dict:
    cam_id = f"CAM-{n:02d}"
    url = connector._url(n)
    desc = CameraDescriptor(camera_id=cam_id, stream_url=url, protocol="RTSP")

    reachable, codec = connector._describe(url)
    row: dict = {
        "camera_id": cam_id,
        "source_index": n,
        "stream_url": url,
        "protocol": "RTSP",
        "codec": codec,
        "reachable": reachable,
        "status": "OFFLINE",
        "frames_read": 0,
        "plate_score": 0.0,
        "triage_note": "",
        "metrics": {},
    }
    if not reachable:
        row["triage_note"] = "RTSP DESCRIBE failed"
        return row

    started = time.time()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    collected: list[np.ndarray] = []
    try:
        if not cap.isOpened():
            row["status"] = "OFFLINE"
            row["triage_note"] = "decoder failed to open"
            row["open_latency_s"] = round(time.time() - started, 1)
            return row

        row["open_latency_s"] = round(time.time() - started, 1)
        for _ in range(frames_to_grab):
            ok, frame = cap.read()
            if ok and frame is not None:
                collected.append(frame)

        row["frames_read"] = len(collected)
        fps = cap.get(cv2.CAP_PROP_FPS)
        row["fps"] = round(fps, 1) if fps and fps > 0 else None
    finally:
        cap.release()

    if not collected:
        row["status"] = "DEGRADED"
        row["triage_note"] = "opened but yielded no frame"
        return row

    # Skip the first few frames: decoders often emit partial frames before a keyframe.
    usable = collected[3:] or collected
    corruption = corruption_ratio(usable)
    last = usable[-1]

    m = frame_metrics(last)
    row["metrics"] = m
    row["width"], row["height"] = m["width"], m["height"]
    row["corruption_ratio"] = corruption
    row["status"] = "DEGRADED" if corruption > 0.5 else "ONLINE"

    score, note = plate_score(m, corruption, len(collected))
    row["plate_score"] = score
    row["triage_note"] = note

    thumb = THUMB_DIR / f"{cam_id}.jpg"
    cv2.imwrite(str(thumb), last, [cv2.IMWRITE_JPEG_QUALITY, 88])
    row["thumbnail_path"] = str(thumb.relative_to(DATA_DIR))

    # Full-resolution crop of the timestamp overlay strip, for OCR in the next stage.
    strip = last[0: max(60, last.shape[0] // 12), :]
    cv2.imwrite(str(THUMB_DIR / f"{cam_id}_overlay.png"), strip)

    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=25, help="frames to sample per camera")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--count", type=int, default=30)
    args = ap.parse_args()

    connector = RTSPConnector(count=args.count)
    print(f"Surveying {args.count} cameras with {args.workers} workers "
          f"({args.frames} frames each)...\n", flush=True)

    rows: list[dict] = []
    started = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(survey_one, n, connector, args.frames): n
                   for n in range(1, args.count + 1)}
        for fut in cf.as_completed(futures):
            n = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:                      # keep the survey going
                row = {"camera_id": f"CAM-{n:02d}", "source_index": n,
                       "reachable": False, "status": "OFFLINE",
                       "plate_score": 0.0, "triage_note": f"error: {exc}"}
            rows.append(row)
            print(f"  {row['camera_id']}  {row.get('status',''):<9} "
                  f"{row.get('codec',''):<6} "
                  f"{row.get('width','?')}x{row.get('height','?'):<5} "
                  f"score={row.get('plate_score',0):>5}  {row.get('triage_note','')[:60]}",
                  flush=True)

    rows.sort(key=lambda r: r["source_index"])
    OUT_JSON.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    ranked = sorted(rows, key=lambda r: r["plate_score"], reverse=True)
    online = [r for r in rows if r.get("status") == "ONLINE"]
    print(f"\nDone in {time.time() - started:.0f}s. "
          f"{len(online)}/{len(rows)} online. Written to {OUT_JSON}")
    print("\nTop cameras for ANPR:")
    for r in ranked[:10]:
        print(f"  {r['camera_id']}  score={r['plate_score']:>5}  {r.get('triage_note','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
