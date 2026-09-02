"""Seed the camera registry from survey + overlay OCR + geocoding.

Combines three evidence sources into the registry:
  1. survey_cameras.py  -- codec, resolution, fps, health, plate-readability score
  2. overlay OCR        -- the real site name and the recording's wall-clock time
  3. geocoding          -- site name -> lat/lon so the GIS map shows the real city

Cameras are grouped into "time clusters": the overlay clock shows these are
independently recorded loops, and only cameras whose recordings overlap in time
can possibly show the same vehicle. That grouping decides where AI runs.

Usage:
    python -m scripts.seed_registry [--no-geocode] [--ai-top 8]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR  # noqa: E402
from app.connectors.catalogue import CatalogueConnector, write_catalogue  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services import registry as reg  # noqa: E402

SURVEY_JSON = DATA_DIR / "camera_survey.json"
OVERLAY_JSON = DATA_DIR / "overlay_reads.json"
GEOCODE_CACHE = DATA_DIR / "geocode_cache.json"
CATALOGUE = DATA_DIR / "cameras.json"

# Ahmedabad city centre -- all these are Ahmedabad ITMS cameras, so searches are
# biased to the city and anything landing outside the bounding box is rejected.
AHMEDABAD = (23.0225, 72.5714)
BBOX = (22.85, 72.35, 23.20, 72.80)          # min_lat, min_lon, max_lat, max_lon

# Landmark-level coordinates for Ahmedabad ITMS sites the geocoder cannot resolve.
# These are APPROXIMATE -- good enough to draw a believable route across the city,
# and flagged as such everywhere they surface so nobody mistakes them for surveyed
# positions. Cameras whose site is genuinely unidentifiable are left UNKNOWN and
# are not drawn on the map at all; they are set through the manual-location UI.
#
#   camera_id -> (lat, lon, basis)
APPROXIMATE_SITES: dict[str, tuple[float, float, str]] = {
    "CAM-01": (23.0064, 72.5698, "Chimanbhai Patel Bridge over the Sabarmati, from overlay label"),
    "CAM-03": (23.1092, 72.5876, "ONGC office, Chandkheda, from overlay label 'O.N.G.C.Office'"),
    "CAM-04": (23.0146, 72.5679, "Paldi junction: 'V.S. Hospital' and 'PALDI' signage visible in frame"),
    "CAM-13": (23.0284, 72.5541, "C.N. Vidyalaya, Ambawadi, from overlay label 'CN-VIDHYALAYA'"),
    "CAM-30": (22.9971, 72.6047, "Rambaug, Maninagar, from overlay label 'GDM-Rambaugh'"),
}

# Noise the OCR picks up alongside the real site label.
_STRIP_TOKENS = (
    "PTZ1", "PTZ2", "PTZ-1", "PTZ-", "PTZ", "FIX1", "FIX-2", "FIX", "RLVD", "RILV",
    "P1", "P2", "P3", "B1", "AI IPC", "CP IP Cam", "Camera 01", "SHOWROOM",
)


def clean_site_name(raw: str) -> str:
    """Turn an OCR'd overlay label into something geocodable."""
    if not raw:
        return ""
    name = raw
    for tok in _STRIP_TOKENS:
        name = name.replace(tok, " ")
    # Drop the ITMS asset code, e.g. "CSITMS-32", "BS-103"
    parts = [p for p in name.replace("_", " ").split()
             if not any(c.isdigit() for c in p) or len(p) > 12]
    name = " ".join(parts)
    return " ".join(name.split()).strip(" -.,")


def _query_variants(name: str) -> list[str]:
    """Overlay labels are abbreviated ITMS names, so try progressively looser forms."""
    base = name.strip()
    words = base.split()
    variants = [f"{base}, Ahmedabad, Gujarat, India"]
    # "Visat teen Rasta" / "Dethali Char Rasta" are junctions -- try the plain landmark.
    for suffix in ("teen Rasta", "Char Rasta", "Junction", "Bridge", "Circle"):
        if base.lower().endswith(suffix.lower()):
            stem = base[: -len(suffix)].strip()
            if stem:
                variants.append(f"{stem} Circle, Ahmedabad, Gujarat, India")
                variants.append(f"{stem}, Ahmedabad, Gujarat, India")
    if len(words) > 1:
        variants.append(f"{words[0]}, Ahmedabad, Gujarat, India")
    seen, out = set(), []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def geocode(name: str, cache: dict) -> tuple[float | None, float | None, str]:
    """Resolve a site name to coordinates via Nominatim.

    A result is accepted ONLY if it falls inside the Ahmedabad bounding box.
    Nominatim happily returns a same-named hotel 60 km away for these junction
    labels, and a confidently-wrong pin on a police map is worse than no pin --
    unresolved cameras are left null and set manually through the registry UI.
    """
    if not name:
        return None, None, ""
    if name in cache:
        c = cache[name]
        return c.get("lat"), c.get("lon"), c.get("display", "")

    import httpx

    headers = {"User-Agent": "SentinelNexus/1.0 (hackathon prototype)"}
    for query in _query_variants(name):
        try:
            resp = httpx.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 5,
                        "viewbox": f"{BBOX[1]},{BBOX[2]},{BBOX[3]},{BBOX[0]}",
                        "bounded": 1},
                headers=headers,
                timeout=15.0,
            )
            data = resp.json()
        except Exception:
            data = []
        time.sleep(1.1)                               # Nominatim asks for <=1 req/s

        for hit in data:
            lat, lon = float(hit["lat"]), float(hit["lon"])
            if BBOX[0] <= lat <= BBOX[2] and BBOX[1] <= lon <= BBOX[3]:
                display = hit.get("display_name", "")
                cache[name] = {"lat": lat, "lon": lon, "display": display,
                               "matched_query": query}
                return lat, lon, display

    cache[name] = {"lat": None, "lon": None, "display": "",
                   "note": "no in-bbox match; set manually"}
    return None, None, ""


def assign_time_clusters(overlay: dict, window_minutes: int = 90) -> dict[str, int]:
    """Group cameras whose recordings overlap in time.

    Only cameras in the same cluster can plausibly show the same vehicle, so this
    is what makes cross-camera correlation meaningful rather than decorative.
    """
    stamped = []
    for cam, rec in overlay.items():
        if rec.get("ts"):
            stamped.append((cam, datetime.fromisoformat(rec["ts"])))
    stamped.sort(key=lambda x: x[1])

    clusters: dict[str, int] = {}
    cluster_id = 0
    previous: datetime | None = None
    for cam, ts in stamped:
        if previous is None or ts - previous > timedelta(minutes=window_minutes):
            cluster_id += 1
        clusters[cam] = cluster_id
        previous = ts
    return clusters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--ai-top", type=int, default=8)
    args = ap.parse_args()

    if not SURVEY_JSON.exists():
        print(f"missing {SURVEY_JSON}; run scripts/survey_cameras.py first")
        return 1

    survey = json.loads(SURVEY_JSON.read_text(encoding="utf-8"))
    overlay = json.loads(OVERLAY_JSON.read_text(encoding="utf-8")) if OVERLAY_JSON.exists() else {}
    cache = json.loads(GEOCODE_CACHE.read_text(encoding="utf-8")) if GEOCODE_CACHE.exists() else {}

    clusters = assign_time_clusters(overlay)
    cluster_sizes: dict[int, int] = {}
    for cid in clusters.values():
        cluster_sizes[cid] = cluster_sizes.get(cid, 0) + 1
    # The largest cluster is the usable cross-camera network.
    primary_cluster = max(cluster_sizes, key=cluster_sizes.get) if cluster_sizes else None

    rows = []
    for row in survey:
        cam = row["camera_id"]
        ov = overlay.get(cam, {})
        site = clean_site_name(ov.get("site", ""))

        lat = lon = None
        display = ""
        accuracy = "UNKNOWN"
        source = ""

        # A geocoder hit validated inside the city bbox is the better evidence,
        # so it is tried first and only then the curated landmark table.
        if site and not args.no_geocode:
            lat, lon, display = geocode(site, cache)
            if lat is not None:
                accuracy, source = "GEOCODED", f"Nominatim: {display[:120]}"

        if lat is None and cam in APPROXIMATE_SITES:
            lat, lon, basis = APPROXIMATE_SITES[cam]
            accuracy, source = "APPROXIMATE", basis

        cluster = clusters.get(cam)
        row = dict(row)
        row.update({
            "name": site or cam,
            "location_name": site,
            "department": "SENTINEL-GOV",
            "district": "Ahmedabad" if lat else "",
            "latitude": lat,
            "longitude": lon,
            "location_accuracy": accuracy,
            "location_source": source,
            "overlay_ts": datetime.fromisoformat(ov["ts"]) if ov.get("ts") else None,
            "time_cluster": cluster,
            "hls_url": f"https://cctv.corp8.cloud/cam{row['source_index']:02d}/index.m3u8",
        })
        row["_cluster"] = cluster
        row["_overlay_ts"] = ov.get("ts")
        row["_geocode"] = display
        rows.append(row)

    GEOCODE_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    # AI priority: a camera must be readable AND in the correlatable time cluster.
    for row in rows:
        score = row.get("plate_score", 0.0)
        if primary_cluster is not None and row.get("_cluster") == primary_cluster:
            score += 25.0                     # correlatable cameras are worth more
        row["plate_score"] = round(min(score, 100.0), 1)
        if row.get("_cluster") == primary_cluster:
            row["triage_note"] = (row.get("triage_note", "") +
                                  "; in primary time cluster").strip("; ")

    # The catalogue is the contract: write it, then seed by reading it back, so
    # the same code path runs whether the document came from us or from the portal.
    catalogue_rows = [{
        "id": f"cam{r['source_index']:02d}",
        "camera_id": r["camera_id"],
        "name": r["name"],
        "department": r["department"],
        "district": r["district"],
        "location_name": r["location_name"],
        "lat": r["latitude"],
        "lon": r["longitude"],
        "location_accuracy": r["location_accuracy"],
        "location_source": r["location_source"],
        "rtsp": r["stream_url"],
        "hls": r["hls_url"],
        "codec": r.get("codec", ""),
        "width": r.get("width"),
        "height": r.get("height"),
        "fps": r.get("fps"),
        "status": r.get("status", "UNKNOWN"),
        "plate_score": r.get("plate_score", 0.0),
        "triage_note": r.get("triage_note", ""),
        "time_cluster": r.get("time_cluster"),
        "overlay_ts": r.get("overlay_ts"),
        "source_index": r["source_index"],
    } for r in rows]
    write_catalogue(CATALOGUE, catalogue_rows, source="rtsp-discovery+overlay-ocr")
    print(f"catalogue: wrote {len(catalogue_rows)} cameras to {CATALOGUE}")

    catalogue = CatalogueConnector(CATALOGUE)
    descriptors = catalogue.discover()
    print(f"catalogue: read back {len(descriptors)} cameras "
          f"({sum(d.protocol == 'RTSP' for d in descriptors)} RTSP)")

    # Merge the catalogue read-back with the survey metrics the registry stores.
    by_id = {d.camera_id: d for d in descriptors}
    seed_rows = []
    for r in rows:
        d = by_id.get(r["camera_id"])
        if d is None:
            continue
        merged = dict(r)
        merged.update({
            "stream_url": d.stream_url,
            "hls_url": d.hls_url,
            "protocol": d.protocol,
            "codec": d.codec,
            "width": d.width,
            "height": d.height,
            "fps": d.fps,
            "latitude": d.latitude,
            "longitude": d.longitude,
            "location_name": d.location_name,
        })
        seed_rows.append(merged)

    init_db()
    with SessionLocal() as db:
        counts = reg.ingest_survey(db, seed_rows, ai_top_n=args.ai_top)
        stats = reg.registry_stats(db)

    print(f"registry: {counts}")
    print(f"stats:    {stats}")
    print(f"\ntime clusters (window 90 min), primary = cluster {primary_cluster}:")
    by_cluster: dict[int | None, list[str]] = {}
    for row in rows:
        by_cluster.setdefault(row.get("_cluster"), []).append(
            f"{row['camera_id']}@{(row.get('_overlay_ts') or '')[11:19]}")
    for cid in sorted(by_cluster, key=lambda c: (c is None, c)):
        label = "unstamped" if cid is None else f"cluster {cid}"
        mark = "  <-- PRIMARY" if cid == primary_cluster else ""
        print(f"  {label:<12} ({len(by_cluster[cid]):>2}) {', '.join(sorted(by_cluster[cid]))}{mark}")

    print("\nAI-enabled cameras:")
    for row in sorted(rows, key=lambda r: -r["plate_score"])[:args.ai_top]:
        loc = row["location_name"] or "(no site name)"
        geo = (f"{row['latitude']:.4f},{row['longitude']:.4f} "
               f"[{row['location_accuracy']}]") if row["latitude"] else "no position"
        print(f"  {row['camera_id']}  score={row['plate_score']:>5}  {loc[:30]:<30} {geo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
