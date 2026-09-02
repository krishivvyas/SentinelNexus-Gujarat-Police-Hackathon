# SENTINEL NEXUS — Implementation Plan

**Status:** pre-build (repo empty)
**Today:** 2026-09-02 · **Submission deadline:** 2026-09-07 → **5.5 working days**
**Working dir:** `C:\Users\krish\OneDrive\Desktop\imp\sentinal`

---

## 0. Reconnaissance findings (measured 2026-09-02, not assumed)

Everything below was verified against the live Sentinel endpoints and this machine before writing this plan.

### 0.1 Feed access — RTSP works, HLS does not

| Endpoint | Result |
|---|---|
| `rtsp://103.250.160.189:8554/stream/camNN` | **OPEN, unauthenticated, working** |
| `https://cctv.corp8.cloud/cam04/index.m3u8` | `302 → /auth/login` (needs session) |
| `https://cctv.corp8.cloud/api/ingest` | `302 → /auth/login` (needs session) |

Live capture confirmed on cam04: `1920x1080`, `25 fps`, PTS advancing cleanly at 40 ms/frame.

> **Decision:** build the ingest path on **RTSP over TCP**, which works today with zero credentials.
> Keep the HLS adapter in the connector interface but treat it as auth-gated and optional.
> `/api/ingest` catalogue discovery is **blocked pending credentials** — see Open Question in §7.

### 0.2 Camera inventory — at least 30 live streams

`cam01` … `cam30` all answer RTSP `DESCRIBE` with `200 OK`. `cam00` returns `400 Bad Request`.

Heterogeneity is real and must be handled — this is exactly what the challenge is testing:

| Property | Observed values |
|---|---|
| Codec | **H.264** (most) and **H.265** (cam06, cam12) |
| Resolution | `1920x1080`, `1280x720` |
| Frame rate | `30`, `25`, `20` fps |
| Stream open latency | **6 s to 91 s** — wildly variable, must not block |
| Decode integrity | cam12 (H.265) returned **heavily corrupted / smeared frames** |
| cam16 | opened but yielded **no frame** in 20 reads |

### 0.3 What the footage actually is — the most important finding

The feeds are **Ahmedabad ITMS traffic-surveillance PTZ cameras**, replayed from a recording.

Confirmed from burned-in overlays on sampled frames:

- cam01 — `Chiman bhai Bridge CSITMS-32_PTZ2` — `13-06-2026 23:10:00`
- cam04 — Paldi Junction (V.S. Hospital / PALDI signage) — `13-06-2026 23:10:22`
- cam09 — `New By PassNr 66KV FIX-2 (From Vadla Fatak)` — `13/06/2026 23:11:44 Sat`
- cam12 — `13-06-2026 23:09:50`

Three consequences that drive the whole design:

**(a) Every frame carries a burned-in wall-clock timestamp.** This is our authoritative time source, far better than server wall-clock, because these are *replayed recordings* whose content time has nothing to do with when we decoded them. **We OCR the overlay and use it for every event timestamp in the report.**

> **Correction (measured on Day 1).** An early sample of four cameras suggested the whole
> fleet was one synchronised recording. It is not. Reading the overlay across all 30 cameras
> shows recordings from **at least five different dates** (13 Jun, 14 Jun, 3 Aug, 8 Aug 2026).
> The cameras are independent loops, not a synchronised network. See §0.5 — this materially
> constrains cross-camera tracking and is the single most important thing Day 1 established.

**(b) Every frame carries the real camera location name.** `Chiman bhai Bridge`, `Paldi Junction`, `New By Pass 66KV`. These geocode to genuine Ahmedabad coordinates, so the GIS map shows a real city network rather than invented pins.

**(c) Plate readability is LOW, and this is the project's defining risk.** These are wide-angle overview PTZ cameras at **night**, not dedicated ANPR cameras. On the sampled frames:

- cam04 (busiest): nearest plates are roughly 20–40 px wide, with heavy motion blur and headlight bloom.
- cam01: distant bridge view — plates unresolvable.
- cam09: near-total darkness, no vehicle present.
- cam12: decode corruption.

> **A pipeline that only reports confidently-OCR'd plates may return near-zero rows on this feed.**
> The plan below is built so the demonstration cannot fail because of this. See §3.

### 0.5 Day-1 result — the fleet surveyed, and the correlatable cluster found

`scripts/survey_cameras.py` probed all 30 cameras; `pipeline/overlay.py` then OCR'd the
burned-in clock and site name from each. Results are in `backend/data/camera_survey.json`
and `backend/data/overlay_reads.json`.

**Fleet health: 24 of 30 ONLINE, 6 DEGRADED.**

Heterogeneity is wider than the first sample suggested — this is strong evidence for the
interoperability argument:

| Property | Observed across the fleet |
|---|---|
| Resolution | `960x576`, `1280x720`, `1280x960`, `1920x1080`, `2560x1440` |
| Codec | H.264 and H.265 mixed |
| Reported FPS | 10, 12, 25, 30 — **and nonsense values: CAM-06 reports 90000, CAM-30 reports 200** |
| Open latency | 1.8 s to 275 s |

> The bogus FPS values are the clearest possible proof that `frame_number / reported_fps`
> must never be used for timing. We use the decoder PTS (`CAP_PROP_POS_MSEC`) plus the
> overlay clock instead.

**Concurrency is a real operational constraint.** At 10 simultaneous stream opens, five
cameras (CAM-07..CAM-11) opened but yielded no frames. Re-probed serially, four of the five
recovered immediately and delivered 1920x1080 at 60 frames. The server degrades under
parallel connections, so the worker pool must cap concurrent opens rather than assume a
camera is dead.

**Time clusters — the key strategic finding.** Grouping cameras by their overlay clock
(90-minute window):

| Cluster | Cameras | Window |
|---|---|---|
| **2 — PRIMARY** | **CAM-01, 02, 03, 04, 05, 09, 12, 13, 14 (9 cameras)** | **13 Jun 2026, 23:21–23:37** |
| 1 | CAM-28, CAM-29 | 13 Jun 2026, 11:47 |
| 3 | CAM-21, CAM-27 | 14 Jun 2026, 02:46–04:04 |
| 5 | CAM-26, CAM-30 | 8 Aug 2026, 23:28–23:48 |
| 4 | CAM-20 | 3 Aug 2026, 22:28 |
| — | 14 cameras | no timestamp parsed yet |

**A vehicle can only be traced across cameras inside the same cluster.** Cluster 2 is
therefore the real cross-camera network: nine Ahmedabad ITMS junction cameras covering a
shared 16-minute window. Cross-camera tracking, the route map, and the designated-vehicle
test all target this cluster. AI camera selection is now scored on *plate readability plus
cluster membership*, not readability alone.

Site names recovered from the overlays are genuine Ahmedabad ITMS locations — `Chiman bhai
Bridge CSITMS-32_PTZ2`, `Janpath CSITMS-10_PTZ2`, `O.N.G.C. Office BS-103`, `Visat teen
Rasta CSITMS-31_PTZ1`, `New By Pass Nr 66KV (From Vadla Fatak)`, `CN-VIDHYALAYA P2`,
`DELIGHT P1 RLVD`, `Dethali Char Rasta FIX1`, `GDM-Rambaugh PTZ2`, `Dolatpara Gate PTZ-1`.

**Open issue — geolocation.** Automatic geocoding of these labels is unreliable: Nominatim
returns a same-named hotel 60 km outside the city for "Janpath" and nothing at all for
"Chimanbhai Patel Bridge" or "Visat Circle". Only CAM-05 (Visat, 23.1193/72.5657) resolved
inside the Ahmedabad bounding box. Rather than scatter confidently-wrong pins across a
police map, unresolved cameras are left null and set through the registry's manual-location
UI, which already records every change in `camera_metadata_history`. **Decision needed — see §7.**

### 0.4 This machine

| | |
|---|---|
| Python | 3.12.7 |
| Node / npm | 22.18.0 / 11.6.2 |
| OpenCV | **5.0.0**, FFmpeg backend present (no separate ffmpeg binary required) |
| PyTorch | **2.10.0+cpu** |
| GPU | **none** — `nvidia-smi` absent, `torch.cuda.is_available()` is `False` |
| Docker | **not installed** |
| PostgreSQL / PostGIS | **not installed** |
| ffmpeg (CLI) | not on PATH |
| git | 2.47.0 |

> **CPU-only inference is a hard constraint.** Real-time AI on 30 × 1080p streams is impossible here.
> The architecture must be honest about this: **sampled-frame processing on a selected camera subset**,
> with a documented horizontal-scaling story for the statewide case.

---

## 1. Architecture

```
                         SENTINEL NEXUS
                                |
        +-----------------------+-----------------------+
        |                                               |
  CAMERA REGISTRY                              CONNECTOR LAYER
  SQLite + GeoJSON                        RTSP | HLS | ONVIF | VMS
  30 cams, lat/lon, codec, status            (pluggable adapters)
        |                                               |
        +-----------------------+-----------------------+
                                v
                     STREAM WORKER POOL
              RTSP/TCP · PTS clock · reconnect + backoff
              keyframe wait · corrupt-frame rejection
                                v
                     +----------+----------+
                     |                     |
              OVERLAY OCR            VEHICLE DETECTOR
          (burned-in timestamp     (OpenCV DNN, ONNX)
           + camera location)              |
                     |              +------+------+
                     |              |             |
                     |         PLATE DETECT   ATTRIBUTES
                     |              |        type / colour
                     |          PLATE OCR         |
                     |              |             |
                     +--------------+-------------+
                                    v
                            SIGHTING EVENT
              {plate?, type, colour, cam, lat/lon, ts, conf, evidence.jpg}
                                    v
                     +--------------+--------------+
                     |                             |
              EVENT DATABASE                  WATCHLIST
              (SQLite / SQLAlchemy)         normalise + match
                     |                             |
              CROSS-CAMERA TRACE            ALERT ENGINE
                     |                             |
                     +--------------+--------------+
                                    v
                        FASTAPI  +  WEBSOCKET
                                    v
                     REACT COMMAND CENTRE (Vite)
          Live view · Leaflet GIS · Search · Alerts · Reports
```

**Backend:** FastAPI · SQLAlchemy · SQLite · OpenCV · WebSocket
**Frontend:** React + Vite · Leaflet · MJPEG/hls.js · Tailwind

**Repo layout**

```
sentinal/
  backend/
    app/
      main.py            api/          models/       schemas/
      connectors/        base.py  rtsp.py  hls.py  onvif.py
      pipeline/          worker.py  detect.py  plate.py  ocr.py  overlay.py
      services/          registry.py  watchlist.py  alerts.py  reports.py
    data/  sentinel.db   evidence/
  frontend/  src/
  models/    *.onnx
  docs/      HLD.md  submission/
  implementation.md
```

---

## 2. The 10 pointers, mapped to what actually gets built

| # | Pointer | Concrete build | Risk |
|---|---|---|---|
| 1 | Registry + GIS | SQLite schema, 30 cameras seeded from RTSP probe + overlay-derived locations, Leaflet map | Low |
| 2 | Federation layer | `BaseConnector` ABC → `RTSPConnector` (working), `HLSConnector` (auth-gated), `ONVIFConnector` (stub, documented) | Low |
| 3 | Video pipeline | Worker pool, PTS clock, reconnect + backoff, keyframe wait, corrupt-frame rejection, lazy open/close | **Med** — 91 s opens, H.265 corruption |
| 4 | AI / ANPR | Vehicle detect → plate detect → preprocess → OCR → normalise | **HIGH** — see §3 |
| 5 | Cross-camera tracking | Sighting events, plate search, timeline ordered by overlay timestamp | Med — depends on §4 yield |
| 6 | Watchlist + alerts | Watchlist CRUD, normalised matching, WebSocket alerts, ack + audit | Low |
| 7 | Command centre | React dashboard, every screen wired to the real API | Med — time |
| 8 | GIS + reports | Route polyline on Leaflet, CSV/PDF report, evidence snapshots | Low |
| 9 | Security + scale | JWT, RBAC (admin/operator/analyst), audit log, `.env`; HLD scaling section | Low |
| 10 | Test + submit | 3 evaluation tests, PPT, HLD, 2 videos, output report | Med — time |

---

## 3. The ANPR risk, and how we defeat it

This is the part of the plan worth defending hardest, because it is where the project most plausibly fails.

**The problem.** The evaluator will hand us a registration number and expect us to locate that vehicle across the government feed. But the government feed is night-time wide-angle PTZ overview footage where plates are 20–40 px and motion-blurred. Confident OCR on that is not reliable.

**Four-layer mitigation — all four get built:**

1. **Camera triage (Day 1).** Survey all 30 cameras and score each on plate readability (vehicle proximity, angle, lighting, blur, decode health). Concentrate AI on the best 6–8. Document the ranking — camera selection *is* an engineering result, not a shortcut.

2. **Aggressive plate-recovery preprocessing.** ROI crop at native resolution → 4× upscale → CLAHE → deskew → sharpen → **multi-frame voting** across consecutive sightings of the same tracked vehicle. Reading one plate from ten blurry frames beats reading it from one.

3. **Attribute-based sighting as a first-class citizen.** Every vehicle is logged with type, colour, direction, timestamp and location **even when the plate is unreadable**. Cross-camera tracing then works on `plate OR (type + colour + time-window + direction)`. This is honest, defensible police-grade intelligence, and it means the map is never empty.

4. **Two-track demonstration.** Test 1 uses our own clear footage where full ANPR provably works end to end. Test 2 uses the government feed and shows the same pipeline with real, honestly-reported yield. Documenting *why* yield differs — and that the limitation is camera placement, not the pipeline — is a stronger submission than pretending it reads every plate.

**Explicitly not doing:** fabricating plate reads, hardcoding the evaluator's number, or faking detections. If a plate is not readable we say so and fall back to attributes.

---

## 4. Day-by-day schedule → 2026-09-07

Each day has a **MUST FINISH** line. If a day's must-finish slips, the next day's stretch goals are cut — never the must-finish.

### Day 1 — Wed 2026-09-02 (today, partial) — Foundation + triage
- Scaffold `backend/` and `frontend/`, virtualenv, dependencies
- SQLite schema: `cameras`, `sightings`, `watchlist`, `alerts`, `users`, `audit_log`
- `BaseConnector` + `RTSPConnector`
- **30-camera survey script**: open each, grab frames, record codec / resolution / fps / open-time / health, OCR the overlay for name and timestamp, save a thumbnail
- Geocode overlay location names to lat/lon; seed the registry
- **MUST FINISH:** registry populated with 30 real cameras plus a plate-readability ranking

### Day 2 — Thu 09-03 — Pipeline + detection
- Stream worker: PTS clock, reconnect with exponential backoff, keyframe wait, corrupt-frame rejection, lazy open/close, graceful shutdown
- Overlay-timestamp OCR as the authoritative event time
- Vehicle detector via OpenCV DNN + ONNX, CPU-tuned, sampled frames
- Vehicle attributes: type, colour, direction
- **MUST FINISH:** sightings landing in the database from ≥3 government cameras with correct overlay timestamps

### Day 3 — Fri 09-04 — ANPR + cross-camera
- Plate detection, recovery preprocessing, OCR, Indian-format normalisation (`GJ01AB1234`)
- Multi-frame plate voting
- Evidence snapshot writer
- Cross-camera trace: plate search and attribute search, timeline ordered by overlay timestamp
- **MUST FINISH:** search a plate → get a multi-camera timeline with evidence images

### Day 4 — Sat 09-05 — Watchlist, alerts, dashboard
- Watchlist CRUD, normalised matching, severity
- Alert engine, WebSocket push, acknowledgement and audit trail
- React dashboard: overview, live view, investigation, alerts, camera management
- Leaflet GIS: camera pins and vehicle route polyline
- **MUST FINISH:** a live watchlist hit produces a real-time alert visible in the UI

### Day 5 — Sun 09-06 — Security, reports, hardening
- JWT and RBAC (admin / operator / analyst), protected routes, `.env`, audit log
- CSV/PDF output report with evidence
- Batch-process the full replay loop to build a rich sighting database
- Bug fixing, empty states, error handling
- **MUST FINISH:** downloadable output report and a secured application

### Day 6 — Mon 09-07 — Test, record, submit
- Test 1 (own feed, 2–3 min), Test 2 (government feed), Test 3 (designated vehicle)
- Record both demonstration videos
- PPT (14 sections), HLD, output report, README and credentials
- **MUST FINISH:** complete submission package uploaded

---

## 5. Priority order — what gets protected at all costs

```
RTSP ingest -> vehicle detection -> sighting DB with real timestamps
   -> plate OCR (best-effort) -> cross-camera search -> watchlist -> alert
      -> government-feed demonstration
```

Cut first if time runs out: ONVIF/VMS stubs, PDF styling, PTZ control, multi-camera video-wall polish, Docker packaging.

---

## 6. Honest scope statement (goes into the HLD verbatim)

> Sentinel Nexus is demonstrated on a CPU-only development machine against 30 live government
> RTSP streams. AI processing runs on sampled frames across a selected camera subset rather than
> on all streams continuously. The architecture — pluggable connectors, a metadata event bus, and
> stateless horizontal AI workers — is designed so that statewide deployment to roughly 80,000
> cameras is a matter of adding regional edge nodes and workers, not of redesigning the platform.
> We do not claim this machine processes 80,000 streams.

---

## 7. Open questions

1. **`/api/ingest` credentials.** The catalogue API and HLS both redirect to `/auth/login`. Without credentials the registry is seeded from RTSP probing plus overlay OCR, which works and is arguably a stronger interoperability demonstration. If credentials exist, the catalogue connector is roughly two hours of work. **Not a blocker.**
2. **Own-feed source for Test 1.** Needs clear daytime vehicle footage with legible plates. Options: user-supplied clip, a phone recording, or an open dataset.
3. **Camera geolocation (blocks the GIS map).** 29 of 30 cameras have no trustworthy coordinates because the ITMS overlay labels do not geocode reliably. Three ways forward, none of which involves inventing coordinates:
   - **(a)** I place the well-known junctions (Chimanbhai Bridge, Paldi, Visat, Rambaug, CN Vidyalaya, ONGC) at approximate landmark coordinates, clearly flagged `location_accuracy = APPROXIMATE` in the registry and in the UI.
   - **(b)** You supply the real coordinates if the Sentinel catalogue or a camera list has them.
   - **(c)** Ship the manual-location UI and place pins by hand for the ~9 cameras in the primary cluster, which is all the route map actually needs.
4. Decisions in §8 pending confirmation.

---

## 9. Progress log

**Day 1 — 2026-09-02 — COMPLETE (must-finish met)**

- [x] Project scaffolded; clean Python venv (`.venv-clean`), no Anaconda inheritance
- [x] Dependency stack resolved — PaddleOCR working; PyTorch deliberately excluded
- [x] SQLite schema: cameras, sightings, watchlist, alerts, users, audit_log, camera_metadata_history
- [x] Connector layer: `BaseConnector` contract + working `RTSPConnector`
- [x] All 30 cameras surveyed: codec, resolution, fps, open latency, decode health, thumbnails
- [x] Plate-readability triage scoring
- [x] **Overlay OCR** (pulled forward from Day 2) — timestamps on 16/28, site names on 20/28
- [x] **Time-cluster analysis** — primary 9-camera correlatable cluster identified
- [x] Registry seeded: 30 cameras, 24 online, 8 AI-enabled
- [x] Geolocation resolved by decision (a): `location_accuracy` added to the schema —
      3 GEOCODED, 5 APPROXIMATE, 22 UNKNOWN. Approximate pins carry the basis for
      their coordinates and are flagged wherever they surface; UNKNOWN cameras are
      not drawn on the map and are set through the manual-location UI.

**Day 2 — 2026-09-02 — stream worker complete and verified**

- [x] `pipeline/worker.py` — PTS-driven sampling, bounded concurrency, backoff,
      loop-point detection, non-fatal decoder warnings
- [x] `connectors/catalogue.py` — camera list read from `cameras.json`
- [x] `connectors/hls.py` — HLS fallback transport for blocked-8554 networks
- [x] `data/cameras.json` — catalogue written and read back; registry seeded from it
- [x] `tests/test_worker_timing.py` — 7 tests, all passing
- [x] `scripts/verify_pipeline.py` — live verification harness
- [x] Footprint reduced: data directory 15 MB -> 2 MB
- [ ] ONNX vehicle detector — next
- [ ] Sightings persisted from the primary cluster — next

---

## 10. Field-rules compliance

Verified against the live grid, not assumed. Evidence in `scripts/verify_pipeline.py`
and `tests/test_worker_timing.py`.

| Rule | Status | Evidence |
|---|---|---|
| RTSP forced over TCP | Done | `rtsp_transport;tcp` set in `OPENCV_FFMPEG_CAPTURE_OPTIONS` before `cv2` import, in both `worker.py` and `rtsp.py` |
| HLS fallback when 8554 blocked | Done | `HLSConnector`; `StreamWorker.fallback_url` alternates to HLS after repeated primary failures |
| No timing from `CAP_PROP_FPS` | Done | FPS is stored as registry metadata only. The fleet reports 90000 (CAM-06) and 200 (CAM-30), so it is never used in a calculation |
| No timing from arrival time | Done | Sampling compares PTS to PTS. Measured drift of −17 s over 185 s proves PTS runs on its own clock |
| Inter-frame gaps tolerated | Done | 14 s gap observed on CAM-04 without a reconnect; `read_failure_grace` = 30 consecutive empty reads before giving up |
| Reconnect with backoff, no tight loop | Done | 2 s → 30 s cap, interruptible `Event.wait`. Verified: 30 s on an unreachable feed, 0 frames, no spin |
| Decoder warnings non-fatal | Done | `error while decoding MB …` logged once then suppressed (`OPENCV_FFMPEG_LOGLEVEL=-8`); a live reconnect recovered mid-run |
| Camera list from `cameras.json` | Done | `CatalogueConnector`; seed writes then reads back 30 cameras |
| Mixed H.264/H.265 and resolutions | Done | 960×576 → 2560×1440, both codecs, seeded and streamed |
| Sane across scene discontinuity | Done | `detect_discontinuity()` + 7 unit tests; sampler state resets at the loop point, consumers get a `discontinuity` flag |
| Pace the load / close captures | Done | Global semaphore (2/4/8 by profile); every `_open` paired with `_close` in a `finally` |
| Build against live capture, no downloads | Done | Every frame in this project came from the live grid |

### Measured throughput

| | Before | After |
|---|---|---|
| Frames delivered (180 s, CAM-04 1080p) | 72 | **109** |
| PTS span covered | 95 s | **168 s** |
| Throughput | 0.53× real time | **0.91× real time** |
| Reconnects | 1 | 0 |

The gain came from replacing `cap.read()` with `cap.grab()` + conditional
`cap.retrieve()`. At a 400 ms sample on a 25 fps feed only about one frame in ten is
converted to a numpy array; the rest are advanced through the decoder and dropped
before the colour conversion and buffer copy. This is what makes the pipeline viable
on a CPU-only machine.

### Hardware profiles

`SENTINEL_PROFILE` scales the pipeline to the host without changing behaviour:

| Profile | Sample interval | Concurrent streams | Inference width |
|---|---|---|---|
| `low` | 1000 ms | 2 | 512 px |
| `balanced` (default) | 400 ms | 4 | 640 px |
| `high` | 250 ms | 8 | 736 px |

### Weight

Data directory: **15 MB → 2 MB** (thumbnails downscaled to 480 px previews, scratch
overlay strips deleted, OCR corpus recompressed).

The virtualenv is ~1.05 GB and cannot go much lower with this stack: PaddlePaddle is
360 MB and OpenCV 124 MB on their own. PaddleOCR imports `imgaug` and `albumentations`
unconditionally, so neither can be dropped; `scikit-learn` was removable and is gone.
Runtime cost is kept low instead — OCR is lazily constructed on first use, frames are
downscaled before inference, and only cameras actually being processed are ever opened.

> **Benchmarked and rejected:** `rapidocr-onnxruntime` (PP-OCR on onnxruntime) looked
> like the lighter option, but on real overlay crops it ran **10–14× slower**
> (1133–1712 ms vs 111–311 ms) and missed CAM-27 entirely. PaddleOCR stays.

**Notes for later:** two dependency traps cost time and are worth recording — a
`--system-site-packages` venv mixed Anaconda's numpy-1.x-compiled pandas with numpy 2.x, and
PaddlePaddle and PyTorch clash over DLLs in one process on Windows. The clean venv without
torch resolves both.

## 8. Decisions — CONFIRMED 2026-09-02

| Topic | Decision |
|---|---|
| Detector | **OpenCV DNN (`cv2.dnn`) with an ONNX model** — no ultralytics dependency at inference, CPU-friendly |
| Database | **SQLite + SQLAlchemy**, with a documented Postgres/PostGIS swap path |
| OCR engine | **PaddleOCR** — strongest on small, blurred, angled text; used for both plates and the overlay timestamp |
| ANPR fallback | **Four-layer mitigation per §3** — triage, recovery preprocessing, attribute sightings, two-track demo |
