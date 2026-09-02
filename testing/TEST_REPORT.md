# Sentinel Nexus — Test Report

**Date:** 2026-09-02 · **Commit:** `a8a8022`
**Environment:** Windows 11 · Python 3.12.7 · OpenCV 4.11.0 · CPU-only (no CUDA)
**Host:** 16 logical cores, 16.8 GB RAM

---

## 1. Summary

| Suite | Result |
|---|---|
| Offline test suite | **108 passed, 0 failed** in 10.3 s |
| Live grid checklist | **12 passed, 0 failed, 1 skipped** |
| Low-end simulation | **Passed** with 2.15× headroom |

Two real defects were found by writing these tests. Both are fixed and covered by
regression tests. One checklist item is reported as **skipped rather than passed**
because the condition it tests did not occur during the live window; it is proven
by unit tests instead. Details in §6 and §4.

---

## 2. Offline suite

Run with `cd testing && python -m pytest`. No network, no server, no camera access.

| File | Tests | What it establishes |
|---|---|---|
| `test_field_rules.py` | 34 | Every DO / DON'T rule for the grid |
| `test_plate_validation.py` | 26 | Signage and timestamps are never reported as plates |
| `test_api_security.py` | 18 | API contract, authentication, RBAC, path traversal |
| `test_catalogue.py` | 12 | `cameras.json` is the contract; mixed codecs and resolutions |
| `test_lightweight.py` | 11 | Cost under a constrained CPU budget |
| `test_worker_timing.py` | 7 | Loop point vs. ordinary inter-frame gap |
| **Total** | **108** | **10.3 s** |

Slowest tests are the detection benchmarks (1.3–1.6 s each); everything else is
sub-second.

### Static vs. behavioural checks

Several rules are verified by scanning the source rather than by observing
behaviour. For a rule like *"no timing logic may depend on `CAP_PROP_FPS`"*,
proving the call site does not exist is stronger evidence than watching one run
produce correct output — a behavioural test passes by luck when the camera happens
to report a sane rate.

Those scans strip comments and docstrings first (`helpers.py:code_only`), because
this codebase deliberately quotes the anti-patterns it warns against. An early
version of the scan flagged a docstring that *documents* the `frame_number /
reported_fps` mistake as though it were committing it.

---

## 3. Operating rules — DO / DON'T

| Rule | Verdict | Evidence |
|---|---|---|
| **DO** force RTSP over TCP | Pass | `rtsp_transport;tcp` present in capture options, and set *before* `import cv2` (the option is only read at import). No file requests UDP. |
| **DON'T** trust the reported frame rate | Pass | Static scan: `CAP_PROP_FPS` never appears in arithmetic; the `frame_number / fps` pattern is absent. The grid reports **200 fps** (CAM-30) and **90000 fps** (CAM-06) — both stored as metadata only. |
| **DO** drive timing from PTS | Pass | Sampler differences PTS against PTS; `time.time()` does not appear in the sampling decision. `FrameEvent` carries `pts_ms` and `pts_delta_ms`. |
| **DON'T** assume a constant frame rate | Pass | Gaps of 0.5 s, 2 s, 8.7 s, 14 s and 30 s all classified as normal. Worker tolerates 30 consecutive empty reads before declaring a feed gone. |
| **DO** reconnect with backoff | Pass | 2 s → 4 → 8 → 16 → capped at 30 s, non-decreasing. Uses an interruptible `Event.wait`, not `time.sleep`, so it neither spins nor blocks shutdown. |
| **DON'T** treat join decoder warnings as fatal | Pass | Decode exceptions are caught, logged once, and the loop continues. `OPENCV_FFMPEG_LOGLEVEL=-8` suppresses the per-macroblock noise. |
| **DO** expect a scene discontinuity | Pass | Loop point detected on backward PTS jump; sampler state resets; ingest closes open tracks at the cut so a vehicle from before is never joined to one after. |
| **DON'T** plan on downloading footage | Pass | No recorded-file fallback in the stream path. Every frame in this project came off the live grid. |
| **DO** pace your load | Pass | Global semaphore caps concurrent opens; every `acquire` is paired with a `release` in a `finally`; only triage-selected cameras are opened. |

---

## 4. Pre-submission checklist — against the live grid

Run with `python live_grid_check.py --camera CAM-04 --seconds 150`.
Raw results: `results/live_grid_check.json`.

| Item | Verdict | Measured |
|---|---|---|
| RTSP clients force TCP; remote clients use HLS | **Pass** | TCP pinned; 30 of 30 cameras carry an HLS fallback URL |
| No timing depends on `CAP_PROP_FPS` or arrival time | **Pass** | Rates seen: 10, 12, 20, 25, 30, **200, 90000**. The impossible ones are recorded, never computed with |
| Inter-frame gaps do not crash or stall | **Pass** | Largest gap **16.2 s**, survived with **0 reconnects** |
| Reconnect with backoff implemented and tested | **Pass** | Unreachable feed: 30 s elapsed, **0 frames**, no busy loop |
| Decoder warnings logged, not fatal | **Pass** | Stream survived join; libavcodec noise confined to the log |
| Camera list from `cameras.json`; mixed codecs/resolutions | **Pass** | 30 cameras; H.264 **and** H.265; five resolutions: 960×576, 1280×720, 1280×960, 1920×1080, 2560×1440 |
| Sane across a scene discontinuity | **Skipped** | No loop point occurred in the 150 s window — see below |
| Grid reachable and consumed live | **Pass** | 8 of 8 probed cameras answered RTSP DESCRIBE in 3 s |
| Captures closed when finished | **Pass** | Worker released its stream slot on exit |

### The skipped item, stated plainly

Scene-discontinuity handling is **not** confirmed by field observation. The feeds
loop, but no loop point fell inside the 150-second capture window, so there was
nothing to observe. The runner reports `SKIP`, not `PASS`, when this happens —
recording it as a pass would misrepresent what was tested.

The logic itself is exhaustively unit-tested: backward jump beyond the threshold is
a discontinuity, gaps up to 30 s are not, boundaries are exact, the first frame is
never one, and the ingest layer closes open tracks at the cut. What remains unproven
is only that a *real* loop point behaves as the model predicts.

### Live capture detail (CAM-04, 150 s)

- 72 frames delivered, PTS span **133 s** over **151 s** wall — **0.88× real time**
- 0 reconnects, 0 discontinuities
- Throughput is network/decode bound, not waste: the worker uses `grab()` and only
  pays for `retrieve()` on frames it keeps

---

## 5. Low-end machine

### What was and was not tested

**This host is not a low-end machine** — 16 logical cores, 16.8 GB RAM. Any claim
of "runs on low-end hardware" measured here would be dishonest.

Instead the benchmark **constrains the process** to 2 OpenCV threads under the
`low` profile and measures the cost. That is a *simulation of* a small machine, not
a substitute for one. Real low-end hardware — 2 cores, 4 GB RAM, slow storage —
**remains unverified**.

### Measured under constraint (2 threads, `low` profile)

Raw results: `results/lightweight_benchmark.json`.

| Measure | Result |
|---|---|
| Detection, 1920×1080 (n=20) | **233 ms median** · min 140 · p90 317 · max 329 |
| Sustained rate | **4.29 detections/s** |
| Required by `low` profile | 2.0 detections/s (1 frame/s × 2 cameras) |
| **Headroom** | **2.15×** |
| Resident memory, detector loaded | **317 MB** |
| 1280×720 median | 202.5 ms |
| 2560×1440 median | 232.5 ms |
| **Cost ratio for 4× the pixels** | **1.15×** |

That last row is the important one. A 1440p camera costs only 1.15× a 720p camera
despite carrying four times the pixels, because frames are downscaled to the
profile's inference width before the forward pass. **Cost is bounded by the profile,
not by whatever resolution a department happened to install.**

> An earlier single-run measurement suggested 0.8× — i.e. the larger frame appearing
> *cheaper*. That was run-to-run noise on a busy host. Repeating with n=20 gives
> 1.15×, which is the figure to trust. The conclusion is unchanged; the number is not.

### Structural properties verified

- No `torch`, `tensorflow` or `ultralytics` in the environment — inference is
  entirely OpenCV DNN
- Detector explicitly targets `DNN_TARGET_CPU` with `DNN_BACKEND_OPENCV`
- OCR engine is lazily constructed: plate validation and normalisation run without
  instantiating it, so a counting-only deployment never pays for it
- UI needs no Node, bundler or `node_modules`; single page under 200 KB
- Database is a single SQLite file — no server required
- `low` profile reduces every cost lever: 2 streams (vs 4), 1000 ms sampling
  (vs 400), 512 px inference (vs 640) — an ≥4× workload reduction

---

## 6. Defects found

### 6.1 `low` profile was silently ignored — *fixed*

`Settings(profile="low")` returned the **balanced** profile. The field carried a
`validation_alias` of `SENTINEL_PROFILE`, which made it settable *only* by that env
name; the keyword argument was accepted and discarded.

Impact: anyone constructing settings in code — including the benchmark — got
balanced behaviour while believing they had constrained the system. Fixed with
`populate_by_name=True`. Covered by `test_profiles_scale_the_pipeline` and
`test_low_profile_reduces_every_cost_lever`.

### 6.2 Memory probe reported 0 MB — *fixed*

The `ctypes` call to `GetProcessMemoryInfo` failed silently and returned 0, then
`nan`. The 64-bit handle and struct pointer need explicit `argtypes`/`restype` or
they are marshalled wrongly. Fixed; the probe now reports 317 MB.

This one is worth noting because a silently-zero measurement is worse than no
measurement — it would have been published as a flattering result.

### 6.3 Earlier: plate false positives — *fixed before this report*

The first live ANPR run reported 23 "plates", all junk: the camera's own burned-in
timestamp (`14-06-2026` → `IA062026`) and street signage (`PALDI JUNCTION` →
`PALOLJUNCTIC`). A read must now match the registration layout **and** carry a valid
state code **and** a real RTO number; vehicle boxes overlapping the overlay bands
are excluded from ANPR entirely.

`test_plate_validation.py` is built from the exact strings that leaked — 26 tests,
including all 16 junk strings as explicit negative cases.

---

## 7. Known limitations

| Limitation | Status |
|---|---|
| Real low-end hardware | Unverified — simulated only (§5) |
| Live scene discontinuity | Unverified — unit-tested only (§4) |
| Government-feed plate yield | **0 plates from 9,645 sightings.** Measured limit: the chain reads plates ≥60 px; these night PTZ cameras present 20–40 px. Attribute correlation carries this feed |
| Camera geolocation | 9 of 31 positioned; the rest are excluded from the map rather than guessed |
| Sustained throughput | 0.88× real time per 1080p stream; concurrency capped accordingly |
| Demo videos and slide deck | Not produced — require screen recording |

---

## 8. Reproducing

```bash
cd testing

# Offline suite — 108 tests, ~10 s
../.venv-clean/Scripts/python.exe -m pytest

# With the measured benchmark numbers printed
../.venv-clean/Scripts/python.exe -m pytest -s -m slow

# Live grid checklist — needs network, ~3 minutes
../.venv-clean/Scripts/python.exe live_grid_check.py --camera CAM-04 --seconds 180

# Catalogue and reachability probes only, no capture
../.venv-clean/Scripts/python.exe live_grid_check.py --quick
```

Results are written to `results/live_grid_check.json` and
`results/lightweight_benchmark.json`.
