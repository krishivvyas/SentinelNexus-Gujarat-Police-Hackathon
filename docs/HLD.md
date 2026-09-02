# Sentinel Nexus — High-Level Design

**Version 1.0 · September 2026**
Unified CCTV interoperability and intelligence platform for statewide surveillance networks.

---

## 1. Problem

Government CCTV estates grow department by department. Each buys its own cameras, its own
VMS and its own vendor stack, so a state ends up with tens of thousands of cameras that
cannot be searched, correlated or acted on as one network.

Concretely, on the Sentinel grid we measured:

- Two codecs (H.264 and H.265) on one network
- Five distinct resolutions, from 960×576 to 2560×1440
- Frame rates of 10, 12, 25 and 30 — **and cameras reporting 90000 and 200 fps**
- Stream-open latency from 1.8 s to 275 s
- 6 of 30 cameras degraded, returning corrupt frames or none at all

An operator asked "where has this vehicle been?" has no way to answer across that estate.

## 2. Current challenges

| Challenge | Consequence |
|---|---|
| Vendor and protocol fragmentation | No single pane of glass; integration is bespoke per department |
| No common metadata model | Cameras cannot be searched, filtered or mapped consistently |
| Manual video review | An investigator scrubs footage camera by camera |
| No cross-camera correlation | A vehicle's route across a city cannot be reconstructed |
| Rip-and-replace proposals | Politically and financially impossible at state scale |

## 3. Proposed model — a layer, not a replacement

Sentinel Nexus is an **overlay**. Existing departmental systems keep running exactly as
they are; the platform federates them through pluggable connectors and adds a common
interoperability and intelligence layer above.

```
   Dept A VMS      Dept B RTSP      Dept C ONVIF      Future protocol
        |                |                |                  |
        +----------------+----------------+------------------+
                                 |
                        CONNECTOR LAYER
              each adapter emits the SAME CameraDescriptor
                                 |
                          SENTINEL NEXUS
             registry · pipeline · AI · correlation · alerts
```

Nothing above the connector layer knows which protocol a camera speaks. Onboarding a new
vendor is a new connector class, not a change to the platform.

## 4. Architecture

```
                         SENTINEL NEXUS
                                |
        +-----------------------+-----------------------+
        |                                               |
  CAMERA REGISTRY                              CONNECTOR LAYER
  catalogue + health + GIS                RTSP | HLS | ONVIF | VMS
        |                                               |
        +-----------------------+-----------------------+
                                v
                     STREAM WORKER POOL
              RTSP/TCP · PTS clock · reconnect + backoff
              bounded concurrency · loop-point detection
                                v
                     +----------+----------+
              OVERLAY OCR            VEHICLE DETECTOR
          (timestamp + site)         (OpenCV DNN, ONNX)
                     |                     |
                     |          PLATE DETECT -> RESTORE -> OCR
                     |                     |     + ATTRIBUTES
                     +----------+----------+
                                v
                    TRACKER (IoU, per camera)
                 multi-frame plate voting per vehicle
                                v
                          SIGHTING EVENT
         {plate?, type, colour, direction, cam, lat/lon, ts, evidence}
                                v
        +-----------------------+-----------------------+
   EVENT DATABASE          WATCHLIST              CROSS-CAMERA TRACE
        |                       |                        |
        +-----------------------+------------------------+
                                v
                   ALERT ENGINE -> WebSocket
                                v
                  FASTAPI  +  COMMAND CENTRE UI
             live · GIS map · investigation · alerts · reports
```

## 5. Components

| Component | Responsibility | Module |
|---|---|---|
| Connector layer | Protocol adapters producing one common representation | `app/connectors/` |
| Catalogue | Camera list read from `cameras.json`, file or authenticated URL | `connectors/catalogue.py` |
| Registry | Source of truth for cameras; every change audited | `services/registry.py` |
| Stream worker | PTS-sampled frames, reconnect, loop detection | `pipeline/worker.py` |
| Overlay OCR | Burned-in clock and site name | `pipeline/overlay.py` |
| Detector | Vehicle detection on OpenCV DNN | `pipeline/detect.py` |
| Plate pipeline | Localisation, restoration, OCR, normalisation, voting | `pipeline/plate.py`, `ocr.py` |
| Tracker | Associates detections across frames | `pipeline/track.py` |
| Ingest | Orchestrates the above, writes sightings | `services/ingest.py` |
| Watchlist | Normalised storage and tolerant matching | `services/watchlist.py` |
| Alert engine | Raises, deduplicates, broadcasts, audits | `services/alerts.py` |
| Search | Plate and attribute cross-camera correlation | `services/search.py` |
| Reports | CSV detection log and PDF evidence report | `services/reports.py` |
| Security | JWT, RBAC, audit trail | `services/security.py` |

## 6. Data flow

1. **Discovery** — a connector enumerates cameras and writes `cameras.json`.
2. **Triage** — every camera is probed for codec, resolution, health, and scored for
   plate readability. Only cameras worth the CPU are enabled for AI.
3. **Ingest** — one worker thread per enabled camera pulls PTS-sampled frames.
4. **Time** — the burned-in overlay clock is OCR'd and interpolated with PTS. This is the
   authoritative event time; server wall-clock is meaningless for replayed footage.
5. **Detect** — vehicles are detected, described (type, colour), and tracked by IoU.
6. **ANPR** — the largest vehicles get plate localisation, restoration and OCR, with votes
   accumulated across frames of the same tracked vehicle.
7. **Persist** — one sighting per tracked vehicle, with an evidence snapshot.
8. **Match** — plates are checked against the watchlist; hits raise alerts.
9. **Correlate** — investigators search by plate or by description and get a timeline.

## 7. Integration approach

`BaseConnector` defines three operations: `discover()`, `probe()` and `frames()`. Any
source implementing them joins the platform.

| Connector | Status | Notes |
|---|---|---|
| `RTSPConnector` | Working | Primary transport, TCP-forced. RTSP DESCRIBE for cheap discovery |
| `HLSConnector` | Working | Fallback where port 8554 is blocked; supports session cookie or bearer token |
| `CatalogueConnector` | Working | Reads `cameras.json` from file or authenticated endpoint |
| `ONVIFConnector` | Documented | Profile-S discovery; the same three methods |
| Vendor VMS | Documented | One adapter per VMS REST API |

## 8. AI architecture

Detection runs entirely inside **OpenCV's DNN module**. There is no PyTorch, TensorFlow or
ultralytics dependency at inference — the model is a weights file OpenCV loads and runs on
CPU. This keeps the deployment to one Python process on commodity hardware.

```
frame -> downscale to profile width -> OpenCV DNN forward pass
      -> vehicle boxes (car/truck/bus/motorcycle/bicycle)
      -> colour estimate (HSV, achromatic paint separated from hue)
      -> IoU tracker
      -> largest vehicles only: plate localisation (morphology)
      -> restore: upscale -> CLAHE -> bilateral -> unsharp -> deskew
      -> OCR over several restorations
      -> position-aware normalisation to the registration layout
      -> multi-frame voting -> consensus plate
```

**Measured plate-recovery limit.** The chain reads plates reliably down to about **60 px**
of plate width; at 40 px reads become unreliable and at 28 px nothing is recovered. This
number defines where ANPR is viable and is the basis for camera triage.

**False positives are the real risk, and are guarded explicitly.** A traffic scene is full
of text — the camera's own burned-in timestamp, shop hoardings, road signs — and all of it
OCRs cleanly. Only a string matching the registration layout *with a valid state code and a
real RTO number* is reported as a plate; everything else yields an unreadable result and the
sighting is recorded on its attributes. This is enforced by a regression test suite built
from strings an earlier build wrongly reported as plates.

## 9. Watchlist architecture

Plates are stored normalised, so matching never depends on how an entry was typed. Matching
is exact first, then falls back to a tolerant comparison that permits up to two *plausible*
OCR confusions (0/O, 1/I, 5/S, 8/B, …) and rejects any difference OCR would not produce.

## 10. Alert workflow

```
sighting with plate -> normalise -> watchlist lookup
   -> hit? -> dedup (same plate + camera within 2 minutes)
           -> raise alert (severity from the watchlist entry)
           -> write audit record
           -> broadcast over WebSocket to every connected dashboard
           -> operator acknowledges -> resolves (both audited)
```

## 11. Security

| Control | Implementation |
|---|---|
| Authentication | JWT bearer tokens, bcrypt password hashing |
| Authorisation | Three roles — ADMIN, OPERATOR, ANALYST — enforced per endpoint |
| Audit trail | Logins, watchlist edits, alert actions, camera metadata changes |
| Secrets | Environment/`.env` only; the app warns if the dev JWT secret is in use |
| Transport | TLS at the reverse proxy; RTSP forced over TCP |
| Path safety | Evidence served only from inside the evidence directory |

Role enforcement is verified: an ANALYST token receives HTTP 403 on admin endpoints and an
unauthenticated request receives 401.

## 12. Scalability

The prototype runs on one CPU-only machine. The architecture is designed so statewide
deployment is a matter of adding nodes, not redesigning:

```
                    CENTRAL CORE
              registry · search · alerting
                         |
                  Event / metadata bus
                         |
       +-----------------+-----------------+
       v                 v                 v
   Region A          Region B          Region C
   Edge node         Edge node         Edge node
   detect+ANPR       detect+ANPR       detect+ANPR
       |                 |                 |
    Cameras           Cameras           Cameras
```

Principles: process at the edge and move **metadata, not video**; stateless AI workers that
scale horizontally; regional fault isolation; the catalogue as the contract so onboarding is
configuration. For the prototype the event path is in-process; at scale it becomes a message
bus (RabbitMQ, or Kafka for statewide throughput), and SQLite becomes PostgreSQL/PostGIS via
a `DATABASE_URL` change — all persistence goes through SQLAlchemy, so no application rewrite.

**Honest scope statement.** Sentinel Nexus is demonstrated on a CPU-only machine against 30
live RTSP streams. AI runs on sampled frames across a triage-selected camera subset, not on
all streams continuously. We do not claim this machine processes 80,000 streams.

## 13. Technology

Python 3.12 · FastAPI · SQLAlchemy · SQLite (Postgres/PostGIS ready) · OpenCV (video +
DNN inference) · PaddleOCR · Leaflet · vanilla JS. No Node build step, no GPU, no Docker
requirement. All components are open source.

## 14. Benefits

- **Preserves existing investment** — departments keep their infrastructure
- **One operational picture** — every camera searchable, mappable, health-monitored
- **Investigation in seconds** — a vehicle's cross-camera timeline instead of manual review
- **Works when plates cannot be read** — attribute correlation keeps the network useful
- **Runs on modest hardware** — profiles scale it from an old laptop upward
- **Auditable** — every operator action and metadata change is recorded

## 15. Prerequisites and assumptions

**Prerequisites:** Python 3.12; network reach to camera streams (RTSP/8554 or HLS with
credentials); ~1 GB disk for dependencies.

**Assumptions:**
- The catalogue (`cameras.json`) is the contract for camera identity and stream URLs.
- Camera coordinates come from the operator. Where they were unavailable we mark accuracy
  honestly — `VERIFIED`, `GEOCODED`, `APPROXIMATE` or `UNKNOWN` — and never draw a camera
  we cannot place.
- The demonstration watchlist is representative, as the challenge permits; it is not real
  stolen-vehicle data.
- Government-feed plate yield is limited by camera placement, not by the pipeline. The
  cameras are wide-angle night overview PTZ units, not dedicated ANPR units.

## 16. Known limitations

- Plate recovery needs roughly 60 px of plate width; the government night PTZ feeds
  present 20–40 px, so plate yield on that feed is effectively zero and the platform falls
  back to attribute correlation. This is stated rather than hidden.
- 22 of 30 cameras have no trustworthy coordinates and are excluded from the map until set.
- The grid's cameras are independent recordings spanning several dates, so cross-camera
  routes are only built within a shared recording window.
- Sustained throughput is about 0.9× real time per 1080p stream on the test machine;
  concurrency is capped accordingly.
