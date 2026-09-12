# Sentinel Nexus

A unified CCTV interoperability and intelligence platform for statewide surveillance
networks — camera federation, live video processing, ANPR, cross-camera vehicle
tracking, watchlist alerting and GIS command and control.

Built against the Sentinel government CCTV grid: **30 live Ahmedabad ITMS traffic
cameras**, mixed H.264/H.265, resolutions from 960×576 to 2560×1440.

> **Design principle:** existing departmental systems keep running. Sentinel Nexus adds a
> common interoperability and intelligence layer on top rather than replacing the
> infrastructure underneath.

---

## Status

This is an active build against a 2026-09-07 deadline. What is real today:

| Component | Status |
|---|---|
| Camera registry + catalogue (`cameras.json`) | **Working** — 30 cameras, 24 online, 6 degraded |
| Connector/federation layer (RTSP, HLS, catalogue) | **Working** |
| Live stream worker (PTS, backoff, loop handling) | **Working**, verified against the live grid |
| Burned-in overlay OCR (timestamp + site name) | **Working** — authoritative event clock |
| Camera triage + time-cluster analysis | **Working** — plate score + cluster per camera |
| Vehicle detection (YOLO11 on ONNX Runtime) | **Working** — 1.83x the vehicles YOLOv4-tiny found on this grid, at the same cost |
| ANPR (localise → restore → OCR → normalise → vote) | **Working** — reads plates down to ~60 px |
| Cross-camera tracking and investigation | **Working** — plate, attribute and free-text search |
| Live MJPEG preview with detections drawn on | **Working** — plain `<img>`, no player library |
| GIS context layers (districts, highways, POIs) | **Working** — fetched live from OpenStreetMap and cached |
| Bulk registry import with column auto-mapping | **Working** — reads a department's own spreadsheet, shows every decision |
| Measured camera health (probe, not trust) | **Working** — reported vs confirmed availability |
| Watchlist + real-time alerts (WebSocket) | **Working** |
| GIS map, CSV/PDF reports | **Working** |
| JWT auth + RBAC + audit trail | **Working** — ADMIN / OPERATOR / ANALYST |
| REST API + command-centre UI | **Working** — 34 REST endpoints + 1 WebSocket, no build step |

Full plan, measurements and daily schedule: [`implementation.md`](implementation.md).
Architecture and design rationale: [`docs/HLD.md`](docs/HLD.md).

### Run it

```bash
cd backend
../.venv-clean/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000>. Demo accounts: `admin` / `operator` / `analyst`,
password `sentinel-<role>`. Interactive API docs are at `/docs`, liveness at `/health`.

### Or use the One-Click Runner (`run.py`)

Creates the virtualenv, installs the pinned dependencies, downloads the detector
weights, seeds the database if it is missing, then starts the server and opens the
browser:

```bash
python run.py
```
*(or `py run.py` on Windows)*

---

## What we learned from the feed

These findings shaped the architecture and are worth reading before changing anything.

- **RTSP works unauthenticated; HLS does not.** `rtsp://103.250.160.189:8554/stream/camNN` is directly reachable. The HLS endpoint and `/api/ingest` both redirect to `/auth/login`. RTSP is the primary transport; HLS is the fallback for networks where 8554 is blocked. (The grid began answering RTSP with `401 WWW-Authenticate: Basic realm="ipcam"` on 2026-09-02 — credentials come from `SENTINEL_RTSP_USER` / `SENTINEL_RTSP_PASSWORD` and are injected at connection time, never stored in the catalogue.)
- **Reported frame rate is unusable.** CAM-06 reports 90,000 fps and CAM-30 reports 200. Nothing in this codebase derives timing from `CAP_PROP_FPS` or from frame arrival time — all timing comes from the decoder PTS and the burned-in overlay clock.
- **Every frame carries a wall-clock overlay and a site name.** These are replayed recordings, so content time has nothing to do with decode time. The overlay clock is the authoritative event timestamp, and the site labels are genuine Ahmedabad locations (Chiman bhai Bridge, Janpath, O.N.G.C. Office, Visat Teen Rasta, CN-Vidhyalaya, GDM-Rambaugh).
- **The cameras are not one synchronised network.** The overlay clocks that could be read span four recording dates and resolve into five time clusters. A vehicle can only be traced across cameras whose recordings overlap in time, so cameras are grouped into *time clusters*. The primary cluster — **CAM-01, 02, 03, 04, 05, 09, 12, 13, 14** — shares a window and is the real cross-camera tracking network. AI camera selection scores plate readability **and** cluster membership.
- **Plate readability is the defining risk.** These are wide-angle night overview PTZ cameras, not dedicated ANPR cameras; plates run 20–40 px with motion blur and headlight bloom. The mitigation is camera triage, plate-recovery preprocessing with multi-frame voting, attribute-based sightings that work without a readable plate, and a two-track demonstration. Detections are never fabricated — if a plate cannot be read, we say so.
- **Vehicle colour is deliberately not derived.** At night the sodium and LED lighting plus headlight bloom drive apparent hue more than the paint does, so a named colour would describe the illuminant. On a record an operator may act on, a confident wrong colour is worse than none, so no colour is produced, stored or displayed. Sightings carry vehicle type and direction instead.

---

## Architecture

```
                         SENTINEL NEXUS
                                |
        +-----------------------+-----------------------+
        |                                               |
  CAMERA REGISTRY                              CONNECTOR LAYER
  SQLite + cameras.json                    RTSP | HLS | CATALOGUE
  30 cams, lat/lon, codec, health         (ONVIF / VMS adapters slot
        |                                  into the same ABC)
        +-----------------------+-----------------------+
                                v
                     STREAM WORKER POOL
              RTSP/TCP · PTS clock · reconnect + backoff
              bounded concurrency · loop-point detection
                                v
                     +----------+----------+
                     |                     |
              OVERLAY OCR            VEHICLE DETECTOR
          (timestamp + site)     (YOLO11 / ONNX Runtime,
                                  YOLOv4-tiny fallback)
                     |                     |
                     |              +------+------+
                     |         PLATE DETECT   ATTRIBUTES
                     |              |        type / direction
                     |          PLATE OCR         |
                     +--------------+-------------+
                                    v
                            SIGHTING EVENT
              {plate?, type, direction, cam, lat/lon, ts, conf, evidence}
                                    v
                     +--------------+--------------+
              EVENT DATABASE                  WATCHLIST
                     |                             |
              CROSS-CAMERA TRACE            ALERT ENGINE
                     +--------------+--------------+
                                    v
                        FASTAPI + WEBSOCKET
                                    v
              COMMAND CENTRE UI (MapLibre GL, map-first)
           layers · video wall · evidence · trace · health
```

Every connector converts its source into the same `CameraDescriptor`, so nothing above
the connector layer knows which protocol a camera speaks. `BaseConnector` is the
abstract adapter; RTSP, HLS and the catalogue are implemented, and ONVIF or a vendor
VMS API would be a new subclass rather than a change anywhere upstream.

---

## Detection

The detector is **YOLO11 on ONNX Runtime**, with YOLOv4-tiny on `cv2.dnn` kept as a
fallback so the platform still detects if the ONNX fetch fails on a restricted
network. Neither path needs PyTorch or the `ultralytics` package — the weights are
consumed as plain `.onnx` graphs, which is what keeps PaddleOCR and the detector able
to live in one Windows process.

Measured over 40 night frames sampled from this grid's own evidence store, on a
16-core CPU-only host, at 1080p:

| Model | Cost / frame | Vehicles / frame | vs YOLOv4-tiny |
|---|---|---|---|
| YOLOv4-tiny (previous) | 75 ms | 1.30 | — |
| **yolo11n** | **40 ms** | **2.33** | **1.79x** |
| **yolo11s** (default) | **80 ms** | **2.38** | **1.83x** |
| yolo11m | 221 ms | 1.95 | 1.50x |

Two things in that table are worth reading carefully.

**The gain is real and it is on the failure mode that matters.** These are wide-angle
night PTZ overviews where a car occupies 40x30 px — the exact case a 2020 detector
trained at 416 px misses outright. Spot-checking the extra boxes against the frames
confirms they are vehicles YOLOv4-tiny simply did not see, not a lowered threshold
inventing them.

**Bigger is worse here.** yolo11m finds *fewer* vehicles than yolo11s while costing
2.8x more. A larger model is better calibrated and therefore more willing to call a
dim night blob "not a vehicle" — which is the wrong trade when the blob usually is
one. So the `high` hardware profile does not load a bigger model; it feeds the same
model more cameras and more frames. yolo11m stays downloadable and can be pinned with
`SENTINEL_DETECTOR=yolo11m` for daylight footage, where that calibration is an asset.

### The plate detector that did not earn its place

A single-class YOLO licence-plate detector is implemented (`pipeline/plate_detect.py`)
and ships **disabled**. Over 55 vehicle crops from this grid's evidence store it
produced **zero** detections — byte-identical output to the existing morphological
localiser, for an extra 39 ms per crop:

| Localiser | Crops with a candidate | Candidates | Cost |
|---|---|---|---|
| Morphology only | 22 / 55 | 36 | 1 ms/crop |
| Learned + morphology | 22 / 55 | 36 | 39 ms/crop |

Raw head scores are ~0.002 on night crops and peak at 0.16 on the brightest frames in
the store — never within reach of any usable threshold. Two different public plate
models agree. Plates here span 20–40 px with motion blur and headlight bloom; there is
nothing to lock onto.

The code path is kept because it is correct and one flag away: point this platform at
a dedicated ANPR camera or daylight footage — exactly the deployment it would scale to
— and `SENTINEL_PLATE_DETECTOR=1` makes it the better localiser immediately. What was
not acceptable was shipping a 39 ms/crop cost that buys nothing measurable.

---

## Command centre UI

Map-first, and served as static files from the same Python process as the API — no
Node toolchain, no bundler, no `node_modules`, no build step. Open `/` and it runs.

The map is the background of the whole application rather than a tab, because the
single thing this platform adds is one surface where every department's cameras exist
together. Everything else floats over it: a layer control, a right-hand slide-over for
detail, and a dockable video wall.

| Panel | What it is for |
|---|---|
| **Layers** | Owning department and ANPR capability as *separate* filters — an operator hunting a registration wants the cameras able to read one, whoever owns them |
| **Cameras** | The estate, with unplaced cameras surfaced rather than silently missing from the map |
| **Registry** | Bulk import from a department's own spreadsheet, with every column-mapping decision shown before anything is saved |
| **ANPR events** | Every reading with its plate crop and boxed full frame attached |
| **Tracing** | A plate's route across cameras, with a playback slider |
| **Watchlist** | Live alerts over a WebSocket; the pin flashes and the map moves to it |
| **Health** | Measured availability against reported availability |
| **Guide** | A scripted walkthrough that drives the real interface rather than playing a recording |

### The basemap, and why there is not one

There is no third-party tile layer by default. Every free dark basemap now either
watermarks anonymous requests (CARTO stamps "API KEY REQUIRED" across every tile) or
refuses application traffic outright (the OSM volunteer servers answer `418`). The
remaining options are an API key on somebody's billing account, or none — and this
platform is specified to run on an isolated operator network where an outbound tile
request may not resolve at all.

So the map draws its geography from layers this platform fetches and caches itself,
from OpenStreetMap via the Overpass API:

| Layer | Features cached |
|---|---|
| District boundaries | 34 |
| National highways | 1,428 |
| Major roads | 7,813 |
| Ahmedabad street grid | 6,071 |
| Police stations | 100 |
| Toll plazas | 198 |
| Railway stations | 668 |

Every one of those counts is the number of features an actual query returned, and the
query is in `services/gis.py` for anyone who wants to re-run it. When a fetch fails the
layer reports itself as unavailable and draws nothing, rather than falling back to
plausible-looking dots — an operator who cannot tell a real toll plaza from a
decorative one cannot use the map to plan an interception.

Operators with their own tile server set `SENTINEL_BASEMAP_URL` and get it underneath
all of this.

---

## Manual Installation & Step-by-Step Setup

### Prerequisites

- **Python 3.12 or 3.13**
- Network access to the Sentinel grid (RTSP port 8554, or HLS with credentials)
- No GPU required. No Docker, no PostgreSQL, no separate FFmpeg binary — OpenCV ships
  its own FFmpeg.

> **Use a clean virtualenv.** Do **not** use `--system-site-packages`, and do not run
> this inside Anaconda's environment. PyTorch is deliberately not a dependency —
> detection runs YOLO11 on ONNX Runtime, a ~15 MB wheel with no CUDA payload.
> Torch would also clash with PaddlePaddle over DLLs on Windows, and PaddleOCR is
> not optional here: it reads the burned-in overlay clock every event timestamp
> comes from.

### Manual Install

```bash
git clone https://github.com/krishivvyas/sentinal.git
cd sentinal

python -m venv .venv-clean
# Windows
.\.venv-clean\Scripts\pip.exe install -r backend/requirements.txt
# Linux / macOS
# ./.venv-clean/bin/pip install -r backend/requirements.txt
```

Copy `.env.example` to `.env` and fill in the RTSP credentials and `JWT_SECRET`.

Download the detector weights. `run.py` fetches all of these automatically if they
are absent, so this is only needed for a manual install:

```bash
# Primary detector — YOLO11s, COCO-trained, pre-exported to ONNX (~38 MB)
curl -L -o models/yolo11s.onnx https://huggingface.co/giangndm/yolo11-onnx/resolve/main/yolo11s_640.onnx

# Fallback detector, used if the ONNX fetch fails on a restricted network (~24 MB)
curl -L -o models/yolov4-tiny.weights https://github.com/AlexeyAB/darknet/releases/download/yolov4/yolov4-tiny.weights

# Optional: lighter and heavier variants for the low/high hardware profiles
# curl -L -o models/yolo11n.onnx https://huggingface.co/giangndm/yolo11-onnx/resolve/main/yolo11n_640.onnx
# curl -L -o models/yolo11m.onnx https://huggingface.co/giangndm/yolo11-onnx/resolve/main/yolo11m_640.onnx
```

Nothing here needs PyTorch or the `ultralytics` package: the weights are consumed as
plain `.onnx` graphs.

### Build the camera registry

```bash
cd backend

# 1. Probe every camera: codec, resolution, fps, health, thumbnails, triage score
python -m scripts.survey_cameras --frames 25 --workers 6

# 2. Read overlay clocks + site names, geocode, cluster by time, seed the registry
python -m scripts.seed_registry --ai-top 8
```

This writes `data/camera_survey.json`, `data/cameras.json` (the catalogue) and
`data/sentinel.db` (the registry).

### Run the web server directly

```bash
cd backend
..\.venv-clean\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

Default users and a representative watchlist are seeded on first startup.

### Verify the pipeline against the live grid

```bash
python -m scripts.verify_pipeline --camera CAM-04 --seconds 180
```

### Run live ingest

```bash
python -m scripts.run_ingest --ai --seconds 600      # triage-selected cameras
python -m scripts.run_ingest --cameras CAM-04 CAM-01 --seconds 300
python -m scripts.run_ingest --cameras CAM-04 --no-anpr    # detection only
```

### Run the own-feed demonstration

The government grid cannot prove the ANPR path end to end, because its plates are
20–40 px at night. This runs the identical pipeline over close-range footage where
plates are legible, and shows detection → ANPR → watchlist → alert firing:

```bash
python -m scripts.demo_own_feed
```

---

## Configuration

Settings come from the environment or a `.env` file — never hardcoded. The file is read
from the repo root first, then `backend/.env`. See `backend/app/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `SENTINEL_PROFILE` | `balanced` | Hardware profile: `low`, `balanced`, `high` |
| `SENTINEL_MAX_STREAMS` | from profile | Hard cap on simultaneously open captures |
| `SENTINEL_RTSP_HOST` / `_PORT` | `103.250.160.189` / `8554` | Grid endpoint |
| `SENTINEL_RTSP_USER` / `_PASSWORD` | empty | Basic auth, injected at connection time |
| `SENTINEL_HLS_COOKIE` / `_TOKEN` | empty | Portal session for the HLS fallback |
| `SENTINEL_CATALOGUE_URL` | empty | Live `cameras.json`; empty falls back to the local file |
| `SENTINEL_PLATE_REGION` | `IN` | Plate layout to validate: `IN` or `GENERIC` |
| `SENTINEL_DETECTOR` | `auto` | `auto`, `yolo11n/s/m/l`, or `yolov4-tiny` to force the fallback |
| `SENTINEL_DETECTOR_THREADS` | `0` | ONNX intra-op threads; 0 = all cores but one |
| `SENTINEL_DETECTOR_CONF` | `0.25` | Detection confidence floor |
| `SENTINEL_PLATE_DETECTOR` | `0` | Learned plate localiser. Off — it measured zero detections on this grid |
| `SENTINEL_BASEMAP_URL` | empty | Raster tile URL. Empty draws the map from our own cached OSM layers |
| `DATABASE_URL` | `sqlite:///backend/data/sentinel.db` | Swap to PostgreSQL/PostGIS here |
| `JWT_SECRET` | dev placeholder | **Must be set in production** |

### Hardware profiles

The platform scales to the machine running it. Profiles change how much of each feed is
sampled and how many streams are open — never behaviour or accuracy.

| Profile | Sample interval | Concurrent streams | Detector | Target |
|---|---|---|---|---|
| `low` | 3000 ms | 2 | yolo11n | Old laptop, no GPU |
| `balanced` | 400 ms | 4 | yolo11s | Typical dev machine |
| `high` | 250 ms | 8 | yolo11s | Many cores |

Note that `high` is not a bigger model. See "Detection" below — on this footage
yolo11m finds *fewer* vehicles than yolo11s while costing 2.8x more, so the extra
CPU buys more cameras and more frames instead.

```bash
SENTINEL_PROFILE=low python -m scripts.verify_pipeline --camera CAM-04
```

---

## The catalogue is the contract

The camera list is read from `cameras.json`, never hardcoded. Onboarding a camera means
editing the catalogue, not the application.

```jsonc
{
  "version": 1,
  "source": "rtsp-discovery+overlay-ocr",
  "cameras": [
    {
      "id": "cam01",
      "camera_id": "CAM-01",
      "name": "Chiman bhai Bridge",
      "department": "SENTINEL-GOV",
      "district": "Ahmedabad",
      "location_name": "Chiman bhai Bridge",
      "lat": 23.0064, "lon": 72.5698,
      "location_accuracy": "APPROXIMATE",
      "location_source": "Chimanbhai Patel Bridge over the Sabarmati, from overlay label",
      "rtsp": "rtsp://103.250.160.189:8554/stream/cam01",
      "hls":  "https://cctv.corp8.cloud/cam01/index.m3u8",
      "codec": "H.264", "width": 1920, "height": 1080, "fps": 30.0,
      "status": "ONLINE", "plate_score": 85.0,
      "triage_note": "high near-field detail; headlight bloom; in primary time cluster",
      "time_cluster": 2,
      "overlay_ts": "2026-06-13 23:24:03",
      "source_index": 1
    }
  ]
}
```

The official `cameras.json` on the Sentinel portal sits behind `/auth/login`. When
credentials are available it drops straight in — no code change:

```python
CatalogueConnector("https://cctv.corp8.cloud/cameras.json", cookie=session_cookie)
```

**`location_accuracy`** is deliberate. `VERIFIED` is operator-supplied, `GEOCODED` was
resolved and validated inside the Ahmedabad bounding box, `APPROXIMATE` is a
landmark-level estimate from the camera's overlay label, and `UNKNOWN` cameras are not
drawn on the map at all. A confidently-wrong pin on a police map is worse than no pin.
Today that is 3 `GEOCODED`, 5 `APPROXIMATE` and 22 `UNKNOWN`.

---

## Project structure

```
sentinal/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── routes.py              # 34 REST endpoints + alert WebSocket
│   │   ├── connectors/                # federation layer — one adapter per protocol
│   │   │   ├── __init__.py
│   │   │   ├── base.py                # BaseConnector ABC + CameraDescriptor contract
│   │   │   ├── catalogue.py           # reads cameras.json (local file or authed URL)
│   │   │   ├── hls.py                 # fallback transport when 8554 is blocked
│   │   │   └── rtsp.py                # primary transport, TCP-forced, RTSP DESCRIBE probe
│   │   ├── models/
│   │   │   └── __init__.py            # cameras, sightings, watchlist, alerts,
│   │   │                              #   users, audit_log, camera_metadata_history
│   │   ├── pipeline/
│   │   │   ├── __init__.py
│   │   │   ├── detect.py              # vehicle detection — YOLO11/ONNX, v4-tiny fallback
│   │   │   ├── onnx_backend.py        # ONNX Runtime inference for the YOLO family
│   │   │   ├── ocr.py                 # plate OCR, normalisation, multi-frame voting
│   │   │   ├── overlay.py             # burned-in timestamp + site-name OCR
│   │   │   ├── plate.py               # plate localisation and image restoration
│   │   │   ├── plate_detect.py        # learned plate localiser (ships disabled — see Detection)
│   │   │   ├── track.py               # IoU tracker (enables plate voting)
│   │   │   └── worker.py              # PTS-driven stream worker, backoff, loop detection
│   │   ├── schemas/                   # Pydantic request/response models  (empty — planned)
│   │   │   └── __init__.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── alerts.py              # alert engine + WebSocket broadcast
│   │   │   ├── gis.py                 # OSM context layers via Overpass, cached to disk
│   │   │   ├── health.py              # camera probing — measured availability
│   │   │   ├── importer.py            # CSV bulk import with column auto-mapping
│   │   │   ├── ingest.py              # per-camera orchestration
│   │   │   ├── live.py                # MJPEG live preview with detections drawn on
│   │   │   ├── registry.py            # camera registry + metadata audit trail
│   │   │   ├── reports.py             # CSV detection log, PDF evidence report
│   │   │   ├── search.py              # cross-camera plate + attribute correlation
│   │   │   ├── security.py            # JWT, RBAC, audit
│   │   │   └── watchlist.py           # normalised storage + tolerant matching
│   │   ├── __init__.py
│   │   ├── config.py                  # env-driven settings + hardware profiles
│   │   ├── db.py                      # SQLAlchemy engine (SQLite → Postgres swap)
│   │   └── main.py                    # FastAPI app; serves the API and the UI
│   ├── static/                        # command-centre UI — no build step, no node_modules
│   │   ├── index.html                 # shell
│   │   ├── index.legacy.html          # the previous single-file tabbed UI, kept for reference
│   │   ├── css/
│   │   │   ├── tokens.css             # design tokens — colour, type, space, motion
│   │   │   └── app.css                # layout and components
│   │   └── js/
│   │       ├── api.js                 # REST client, session, token-in-query URLs
│   │       ├── app.js                 # shell: gate, rail, panel routing, alert socket
│   │       ├── map.js                 # MapLibre map, camera pins, GIS layers, trace
│   │       ├── store.js               # state + subscribe/notify
│   │       ├── tour.js                # guided walkthrough that drives the real UI
│   │       ├── ui.js                  # DOM helpers, icons, formatting, toasts
│   │       ├── wall.js                # video wall dock
│   │       └── panels/                # layers, camera, registry, events, trace,
│   │                                  #   watchlist, health
│   ├── data/                          # generated — most of it is gitignored
│   │   ├── evidence/                  # detection snapshots            (gitignored)
│   │   ├── gis/                       # cached OSM layers (GeoJSON)    (gitignored)
│   │   ├── own_feed/                  # close-range demo footage       (gitignored)
│   │   ├── ocr_samples/               # full-res OCR/ANPR test corpus  (gitignored)
│   │   ├── thumbnails/                # per-camera preview stills      (gitignored)
│   │   ├── camera_survey.json         # per-camera probe results + triage scores
│   │   ├── cameras.json               # THE CATALOGUE — camera list is read from here
│   │   ├── geocode_cache.json         # Nominatim lookups, cached to avoid re-querying
│   │   ├── overlay_reads.json         # OCR'd overlay clocks + site names
│   │   └── sentinel.db                # SQLite registry + events       (gitignored)
│   ├── tests/
│   │   ├── test_importer.py           # column auto-mapping across real spreadsheet shapes
│   │   ├── test_ocr_fallback.py
│   │   └── test_scenery_suppression.py
│   ├── scripts/
│   │   ├── demo_own_feed.py           # own-feed run: detection → ANPR → watchlist → alert
│   │   ├── run_ingest.py              # live ingest against the grid
│   │   ├── seed_registry.py           # catalogue build + geocode + time-cluster + seed
│   │   ├── survey_cameras.py          # fleet probe + plate-readability triage
│   │   └── verify_pipeline.py         # live verification harness against the grid
│   ├── requirements.txt
│   └── requirements-dev.txt
├── docs/
│   ├── HLD.md                         # high-level design
│   └── submission/
│       ├── government_feed_detections.csv   # detection log from the grid (earlier export)
│       └── own_feed_trace_MPE3389.pdf       # evidence report from the own-feed run
├── models/                            # all weights downloaded by run.py, gitignored
│   ├── yolo11s.onnx                   # primary detector           (~38 MB)
│   ├── yolo11n.onnx                   # "low" profile              (~11 MB, optional)
│   ├── yolo11m.onnx                   # pinnable for daylight      (~80 MB, optional)
│   ├── plate-detector.onnx            # learned plate localiser    (~10 MB, ships disabled)
│   ├── yolov4-tiny.cfg                # committed
│   └── yolov4-tiny.weights            # fallback detector          (~24 MB)
├── .env.example                       # copy to .env and fill in
├── .gitignore
├── implementation.md                  # full plan, measurements, daily schedule
├── run.py                             # one-click launcher & environment orchestrator
└── README.md
```

Detector weights, `*.db`, `evidence/`, `thumbnails/`, `ocr_samples/`, `own_feed/` and the
virtualenv are gitignored — clone, download the weights (see Install), then run the two
seed scripts to regenerate the data directory.

---

## Field-rules compliance

The grid has specific operational requirements. Each is implemented, and each was
exercised against the live grid during development:

| Rule | How |
|---|---|
| Force RTSP over TCP | `OPENCV_FFMPEG_CAPTURE_OPTIONS` with `rtsp_transport;tcp`, set before `cv2` import in `pipeline/worker.py` |
| HLS fallback when 8554 blocked | `HLSConnector`; the worker alternates to `fallback_url` after repeated primary failures |
| Never trust `CAP_PROP_FPS` | Read once during the survey and stored as metadata; never used in a calculation |
| Drive timing from PTS | Sampling compares `CAP_PROP_POS_MSEC` to `CAP_PROP_POS_MSEC`, never arrival time |
| Tolerate inter-frame gaps | A 16.2 s PTS gap was survived live with 0 reconnects |
| Reconnect with backoff | 2 s → 30 s cap, interruptible wait, never a tight loop |
| Decoder warnings non-fatal | `OPENCV_FFMPEG_LOGLEVEL=-8`; logged once, then suppressed; self-correct at first IDR |
| Handle scene discontinuity | `detect_discontinuity()` on backward or large-forward PTS jumps; resets sampler and tracker state |
| Pace the load | Global `_OPEN_SEMAPHORE` sized from the profile; every open paired with a close |
| Build against live capture | Every frame from the grid in this project came off the live feed |

### Measured throughput

Replacing `cap.read()` with `cap.grab()` + conditional `cap.retrieve()` — so only frames
we keep pay for colour conversion and the buffer copy (CAM-04, 1080p, 180 s; full
numbers in [`implementation.md`](implementation.md)):

| | Before | After |
|---|---|---|
| Frames delivered | 72 | **109** |
| PTS span covered | 95 s | **168 s** |
| Throughput | 0.53× real time | **0.91× real time** |

---

## Notes and limitations

- **CPU-only.** AI runs on sampled frames across a selected camera subset, not on all 30
  streams continuously. The architecture — pluggable connectors, metadata event bus,
  stateless horizontal workers — is designed so statewide deployment to ~80,000 cameras
  means adding edge nodes and workers, not redesigning the platform. We do not claim this
  machine processes 80,000 streams.
- **Concurrency is a real constraint.** At 10 simultaneous opens, five cameras returned no
  frames; re-probed serially, four recovered immediately. Cameras are not marked dead on a
  single failure.
- **6 of 30 cameras are degraded** — decode corruption or no usable frames.
- **22 cameras have no trustworthy coordinates.** They are excluded from the map until set
  through the registry, which records every change in `camera_metadata_history`.
- **Auto-rickshaw labelling is a heuristic, not a classifier.** The COCO-trained weights
  have no auto class, so a yellow-body-and-aspect-ratio test recovers the common lit case
  and anything ambiguous keeps its original label.
- **The map needs internet.** Leaflet and its tiles are loaded from a CDN; the rest of the
  UI is served from this process and works offline.
- **The venv is ~1 GB.** PaddlePaddle is 360 MB and OpenCV 124 MB; PaddleOCR imports
  `imgaug` and `albumentations` unconditionally so neither can be dropped. Runtime cost is
  kept low instead: OCR is lazily constructed, frames are downscaled before inference, and
  only cameras being processed are opened.
- **No automated test suite ships with this repo.** Verification is done through
  `scripts/verify_pipeline.py` against the live grid and `scripts/demo_own_feed.py` for
  the end-to-end ANPR path.

---

## Licence and attribution

Built with open-source components: OpenCV, PaddleOCR, FastAPI, SQLAlchemy, Leaflet,
ReportLab, and the YOLOv4-tiny weights from the Darknet project.
Camera imagery belongs to the Sentinel/ITMS operator and is used for evaluation only.
