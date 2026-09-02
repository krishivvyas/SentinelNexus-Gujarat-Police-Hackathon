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
| Camera registry + catalogue (`cameras.json`) | **Working** — 30 cameras, 24 online |
| Connector/federation layer (RTSP, HLS, catalogue) | **Working** |
| Live stream worker (PTS, backoff, loop handling) | **Working**, verified against the live grid |
| Burned-in overlay OCR (timestamp + site name) | **Working** — authoritative event clock |
| Camera triage + time-cluster analysis | **Working** |
| Vehicle detection + attributes (OpenCV DNN) | **Working** — ~4,500 sightings ingested |
| ANPR (localise → restore → OCR → normalise → vote) | **Working** — reads plates ≥60 px |
| Cross-camera tracking and investigation | **Working** — plate and attribute search |
| Watchlist + real-time alerts (WebSocket) | **Working** |
| GIS map, CSV/PDF reports | **Working** |
| JWT auth + RBAC + audit trail | **Working** |
| REST API + command-centre UI | **Working** — 25 endpoints, no build step |

Full plan, measurements and daily schedule: [`implementation.md`](implementation.md).
Architecture and design rationale: [`docs/HLD.md`](docs/HLD.md).

### Run it

```bash
cd backend
../.venv-clean/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000>. Demo accounts: `admin` / `operator` / `analyst`,
password `sentinel-<role>`.

---

## What we learned from the feed

These findings shaped the architecture and are worth reading before changing anything.

**RTSP works unauthenticated; HLS does not.** `rtsp://103.250.160.189:8554/stream/camNN`
is directly reachable. The HLS endpoint and `/api/ingest` both redirect to `/auth/login`.
RTSP is the primary transport; HLS is the fallback for networks where 8554 is blocked.

**Reported frame rate is unusable.** CAM-06 reports 90000 fps and CAM-30 reports 200.
Nothing in this codebase derives timing from `CAP_PROP_FPS` or from frame arrival time —
all timing comes from the decoder PTS and the burned-in overlay clock.

**Every frame carries a wall-clock overlay and a site name.** These are replayed
recordings, so content time has nothing to do with decode time. The overlay clock is the
authoritative event timestamp, and the site labels are genuine Ahmedabad locations
(Chimanbhai Bridge, Janpath, ONGC Office, Visat Teen Rasta, CN Vidyalaya, Rambaugh).

**The cameras are not one synchronised network.** Overlay clocks span at least five
recording dates. A vehicle can only be traced across cameras whose recordings overlap in
time, so cameras are grouped into *time clusters*. The primary cluster —
**CAM-01, 02, 03, 04, 05, 09, 12, 13, 14** — shares a window and is the real cross-camera
tracking network. AI camera selection scores plate readability **and** cluster membership.

**Plate readability is the defining risk.** These are wide-angle night overview PTZ
cameras, not dedicated ANPR cameras; plates run 20–40 px with motion blur and headlight
bloom. The mitigation is camera triage, plate-recovery preprocessing with multi-frame
voting, attribute-based sightings that work without a readable plate, and a two-track
demonstration. Detections are never fabricated — if a plate cannot be read, we say so.

---

## Architecture

```
                         SENTINEL NEXUS
                                |
        +-----------------------+-----------------------+
        |                                               |
  CAMERA REGISTRY                              CONNECTOR LAYER
  SQLite + cameras.json                   RTSP | HLS | ONVIF | VMS
  30 cams, lat/lon, codec, health             (pluggable adapters)
        |                                               |
        +-----------------------+-----------------------+
                                v
                     STREAM WORKER POOL
              RTSP/TCP · PTS clock · reconnect + backoff
              bounded concurrency · loop-point detection
                                v
                     +----------+----------+
                     |                     |
              OVERLAY OCR            VEHICLE DETECTOR
          (timestamp + site)         (OpenCV DNN, ONNX)
                     |                     |
                     |              +------+------+
                     |         PLATE DETECT   ATTRIBUTES
                     |              |        type / colour
                     |          PLATE OCR         |
                     +--------------+-------------+
                                    v
                            SIGHTING EVENT
              {plate?, type, colour, cam, lat/lon, ts, conf, evidence}
                                    v
                     +--------------+--------------+
              EVENT DATABASE                  WATCHLIST
                     |                             |
              CROSS-CAMERA TRACE            ALERT ENGINE
                     +--------------+--------------+
                                    v
                        FASTAPI + WEBSOCKET
                                    v
                  COMMAND CENTRE UI (Leaflet GIS)
```

Every connector converts its source into the same `CameraDescriptor`, so nothing above
the connector layer knows which protocol a camera speaks.

---

## Quick start

### Prerequisites

- **Python 3.12**
- Network access to the Sentinel grid (RTSP port 8554, or HLS with credentials)
- No GPU required. No Docker, no PostgreSQL, no separate FFmpeg binary — OpenCV ships
  its own FFmpeg.

> **Use a clean virtualenv.** Do **not** use `--system-site-packages`, and do not run
> this inside Anaconda's environment: Anaconda's numpy-1.x-compiled packages break
> against the numpy this project needs. PyTorch is deliberately not a dependency —
> detection runs on OpenCV DNN, and PyTorch also clashes with PaddlePaddle over DLLs on
> Windows when both load in one process.

### Install

```bash
git clone https://github.com/krishivvyas/sentinal.git
cd sentinal

python -m venv .venv-clean
# Windows
./.venv-clean/Scripts/python.exe -m pip install -r backend/requirements.txt
# Linux / macOS
# ./.venv-clean/bin/python -m pip install -r backend/requirements.txt
```

Download the detector weights (~24 MB, open source; loaded by OpenCV's DNN module —
there is no PyTorch or ultralytics dependency):

```bash
curl -L -o models/yolov4-tiny.weights   https://github.com/AlexeyAB/darknet/releases/download/yolov4/yolov4-tiny.weights
curl -L -o models/yolov4-tiny.cfg   https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg
```

### Build the camera registry

```bash
cd backend

# 1. Probe every camera: codec, resolution, fps, health, thumbnails, triage score
python -m scripts.survey_cameras --frames 25 --workers 6

# 2. Read overlay clocks + site names, geocode, cluster by time, seed the registry
python -m scripts.seed_registry --ai-top 8
```

This writes `data/cameras.json` (the catalogue) and `data/sentinel.db` (the registry).

### Verify the pipeline against the live grid

```bash
python -m scripts.verify_pipeline --camera CAM-04 --seconds 180
```

Checks backoff on an unreachable feed, PTS-driven sampling, gap tolerance, and
throughput against real time.

### Run live ingest

```bash
python -m scripts.run_ingest --ai --seconds 600      # triage-selected cameras
python -m scripts.run_ingest --cameras CAM-04 CAM-01 --seconds 300
```

### Test 1 — own-feed demonstration

Proves the full chain (detection → ANPR → watchlist → real-time alert) on close-range
footage where plates are legible:

```bash
python -m scripts.demo_own_feed
```

### Run the tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q          # 33 tests
```

---

## Configuration

Settings come from the environment or a `.env` file — never hardcoded. See
`backend/app/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `SENTINEL_PROFILE` | `balanced` | Hardware profile: `low`, `balanced`, `high` |
| `SENTINEL_MAX_STREAMS` | from profile | Hard cap on simultaneously open captures |
| `DATABASE_URL` | `sqlite:///data/sentinel.db` | Swap to PostgreSQL/PostGIS here |
| `JWT_SECRET` | dev placeholder | **Must be set in production** |
| `SENTINEL_RTSP_HOST` / `_PORT` | `103.250.160.189` / `8554` | Grid endpoint |

### Hardware profiles

The platform scales to the machine running it. Profiles change how much of each feed is
sampled and how many streams are open — never behaviour or accuracy.

| Profile | Sample interval | Concurrent streams | Inference width | Target |
|---|---|---|---|---|
| `low` | 1000 ms | 2 | 512 px | Old laptop, no GPU |
| `balanced` | 400 ms | 4 | 640 px | Typical dev machine |
| `high` | 250 ms | 8 | 736 px | Many cores |

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
      "location_name": "Chiman bhai Bridge",
      "lat": 23.0064, "lon": 72.5698,
      "location_accuracy": "APPROXIMATE",
      "location_source": "Chimanbhai Patel Bridge over the Sabarmati, from overlay label",
      "rtsp": "rtsp://103.250.160.189:8554/stream/cam01",
      "hls":  "https://cctv.corp8.cloud/cam01/index.m3u8",
      "codec": "H.264", "width": 1920, "height": 1080, "fps": 30,
      "status": "ONLINE", "plate_score": 85.0, "time_cluster": 2
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

---

## Project structure

```
sentinal/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── routes.py              # 25 REST endpoints + alert WebSocket
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
│   │   │   ├── detect.py              # vehicle detection on OpenCV DNN + colour
│   │   │   ├── ocr.py                 # plate OCR, normalisation, multi-frame voting
│   │   │   ├── overlay.py             # burned-in timestamp + site-name OCR
│   │   │   ├── plate.py               # plate localisation and image restoration
│   │   │   ├── track.py               # IoU tracker (enables plate voting)
│   │   │   └── worker.py              # PTS-driven stream worker, backoff, loop detection
│   │   ├── schemas/                   # Pydantic request/response models  (planned)
│   │   │   └── __init__.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── alerts.py              # alert engine + WebSocket broadcast
│   │   │   ├── ingest.py              # per-camera orchestration
│   │   │   ├── registry.py            # camera registry + metadata audit trail
│   │   │   ├── reports.py             # CSV detection log, PDF evidence report
│   │   │   ├── search.py              # cross-camera plate + attribute correlation
│   │   │   ├── security.py            # JWT, RBAC, audit
│   │   │   └── watchlist.py           # normalised storage + tolerant matching
│   │   ├── __init__.py
│   │   ├── config.py                  # env-driven settings + hardware profiles
│   │   ├── db.py                      # SQLAlchemy engine (SQLite → Postgres swap)
│   │   └── main.py                    # FastAPI app; serves the API and the UI
│   ├── static/
│   │   └── index.html                 # command-centre UI — no build step, no node_modules
│   ├── data/                          # generated — not all of it is committed
│   │   ├── evidence/                  # detection snapshots               (empty)
│   │   ├── ocr_samples/               # full-res OCR/ANPR test corpus     (3 files)
│   │   ├── thumbnails/                # 480px camera previews             (28 files)
│   │   ├── camera_survey.json         # per-camera probe results + triage scores
│   │   ├── cameras.json               # THE CATALOGUE — camera list is read from here
│   │   ├── geocode_cache.json         # Nominatim lookups, cached to avoid re-querying
│   │   ├── overlay_reads.json         # OCR'd overlay clocks + site names
│   │   └── sentinel.db                # SQLite registry + events
│   ├── scripts/
│   │   ├── demo_own_feed.py           # Test 1: detection → ANPR → watchlist → alert
│   │   ├── run_ingest.py              # live ingest against the grid
│   │   ├── seed_registry.py           # catalogue build + geocode + time-cluster + seed
│   │   ├── survey_cameras.py          # fleet probe + plate-readability triage
│   │   └── verify_pipeline.py         # live verification harness against the grid
│   ├── tests/
│   │   ├── test_plate_validation.py   # guards against reporting signage as plates
│   │   └── test_worker_timing.py      # loop-point vs inter-frame-gap discrimination
│   ├── requirements.txt
│   └── requirements-dev.txt
├── docs/
│   ├── HLD.md                         # high-level design
│   └── submission/                    # output report + evidence PDF
├── models/                            # detector weights                  (not committed)
├── .gitignore
├── implementation.md                  # full plan, measurements, daily schedule
└── README.md
```

Detector weights, `*.db`, `evidence/` and the virtualenv are gitignored — clone, download
the weights (see Install), then run the two seed scripts to regenerate the data directory.

---

## Field-rules compliance

The grid has specific operational requirements. Each is implemented and verified:

| Rule | How |
|---|---|
| Force RTSP over TCP | `rtsp_transport;tcp` set before `cv2` import |
| HLS fallback when 8554 blocked | `HLSConnector`; worker alternates after repeated failures |
| Never trust `CAP_PROP_FPS` | Stored as metadata only, never used in a calculation |
| Drive timing from PTS | Sampling compares PTS to PTS, never arrival time |
| Tolerate inter-frame gaps | 14 s gap survived without reconnecting |
| Reconnect with backoff | 2 s → 30 s cap, interruptible wait, never a tight loop |
| Decoder warnings non-fatal | Logged once, then suppressed; self-correct at first IDR |
| Handle scene discontinuity | `detect_discontinuity()`, unit-tested, resets sampler state |
| Pace the load | Global semaphore; every open paired with a close |
| Build against live capture | Every frame in this project came off the live grid |

### Measured throughput

Replacing `cap.read()` with `cap.grab()` + conditional `cap.retrieve()` — so only frames
we keep pay for colour conversion and the buffer copy:

| | Before | After |
|---|---|---|
| Frames delivered (180 s, 1080p) | 72 | **109** |
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
- **The venv is ~1 GB.** PaddlePaddle is 360 MB and OpenCV 124 MB; PaddleOCR imports
  `imgaug` and `albumentations` unconditionally so neither can be dropped. Runtime cost is
  kept low instead: OCR is lazily constructed, frames are downscaled before inference, and
  only cameras being processed are opened.

---

## Licence and attribution

Built with open-source components: OpenCV, PaddleOCR, FastAPI, SQLAlchemy, Leaflet.
Camera imagery belongs to the Sentinel/ITMS operator and is used for evaluation only.
