# Testing

Every operating rule for the camera grid is an executable test here, plus a
constrained-resource benchmark and a runner that checks the rules against the
live grid.

```bash
cd testing

# Fast, offline. No network, no server.
../.venv-clean/Scripts/python.exe -m pytest -q

# With the measured numbers printed
../.venv-clean/Scripts/python.exe -m pytest -q -s -m slow

# Against the live grid (needs network, ~3 minutes)
../.venv-clean/Scripts/python.exe live_grid_check.py --camera CAM-04 --seconds 180
../.venv-clean/Scripts/python.exe live_grid_check.py --quick     # DESCRIBE probes only
```

## Files

| File | Covers |
|---|---|
| `test_field_rules.py` | Every DO / DON'T, as static and behavioural checks |
| `test_worker_timing.py` | Loop point vs. ordinary inter-frame gap |
| `test_plate_validation.py` | Guards against reporting signage and timestamps as plates |
| `test_catalogue.py` | `cameras.json` is the contract; mixed codecs and resolutions |
| `test_api_security.py` | API shape, authentication, RBAC, path traversal |
| `test_lightweight.py` | Cost under a constrained CPU budget |
| `live_grid_check.py` | The pre-submission checklist, against the real grid |
| `helpers.py` | Comment/docstring-aware source scanning |
| `TEST_REPORT.md` | Full written report of every result |

Some checks are **static** rather than behavioural. For a rule like "no timing
logic may depend on `CAP_PROP_FPS`", proving the call site does not exist is
stronger evidence than watching one lucky run produce correct output. Those scans
strip comments and docstrings first, because this codebase deliberately quotes the
anti-patterns it warns against.

## The DO / DON'T rules

| Rule | Test |
|---|---|
| DO force RTSP over TCP | `test_rtsp_transport_is_forced_to_tcp`, `test_transport_is_set_before_cv2_import`, `test_udp_transport_is_never_requested` |
| DON'T trust the reported frame rate | `test_no_timing_logic_uses_cap_prop_fps`, `test_frame_number_over_fps_pattern_is_absent` |
| DO drive timing from PTS | `test_sampling_compares_pts_not_wall_clock`, `test_worker_reads_pts_for_every_frame` |
| DON'T assume a constant frame rate | `test_inter_frame_gaps_are_not_discontinuities` (5 cases to 30 s) |
| DO reconnect with backoff | `test_backoff_is_exponential_and_capped_at_30s`, `test_backoff_sleep_is_interruptible_not_a_tight_loop` |
| DON'T treat join decoder warnings as fatal | `test_decoder_warnings_are_logged_once_not_raised`, `test_ffmpeg_log_level_is_quietened` |
| DO expect a scene discontinuity | `test_loop_point_is_detected`, `test_sampler_state_resets_at_a_discontinuity`, `test_ingest_closes_tracks_across_a_cut` |
| DON'T plan on downloading footage | `test_no_recorded_file_fallback_in_the_stream_path` |
| DO pace your load | `test_concurrent_stream_opens_are_capped`, `test_every_open_is_paired_with_a_close` |

## Pre-submission checklist

| Item | Status | Evidence |
|---|---|---|
| RTSP clients force TCP; remote clients use HLS | Pass | `rtsp_transport;tcp` set before `import cv2`; `HLSConnector` + worker fallback |
| No timing depends on `CAP_PROP_FPS` or arrival time | Pass | Static scan; FPS is metadata only. The grid reports 200 and 90000 fps |
| Inter-frame gaps do not crash or stall | Pass | 16.2 s gap survived live with 0 reconnects |
| Reconnect with backoff implemented and tested | Pass | 2 s → 30 s cap, interruptible; unreachable feed produced 0 frames and no spin |
| Decoder warnings logged, not fatal | Pass | Logged once then suppressed; stream survived join |
| Camera list from `cameras.json`; mixed codecs and resolutions | Pass | 30 cameras; H.264 + H.265; five resolutions, 960×576 → 2560×1440 |
| Sane across a scene discontinuity | **Skipped** (live) | Unit-tested exhaustively; no loop point occurred in the 150 s live window |

The last row is the honest one: no loop point occurred during the live run, so
that behaviour is proven by unit tests rather than observed in the field. The
runner reports it as SKIP rather than PASS when it does not happen.

## Low-end machine

**This host is not low-end** — 16 logical cores, 16.8 GB RAM. Claiming a low-end
result from it would be dishonest, so `test_lightweight.py` instead *constrains*
the process to 2 OpenCV threads with the `low` profile and measures the cost.
That is a simulation of a small machine, not a substitute for one.

Measured under that constraint (n=20, `results/lightweight_benchmark.json`):

| Measure | Result |
|---|---|
| Detection, 1920×1080 | **233 ms median** · min 140 · p90 317 |
| Sustained rate | **4.29 detections/s** against 2.0/s required — **2.15× headroom** |
| Resident memory | **317 MB** with the detector loaded |
| 2560×1440 vs 1280×720 | **1.15×** the time for **4×** the pixels |

That last row is the important one. Cost is bounded by the profile's inference
width, not by whatever resolution a department installed, so a 1440p camera costs
barely more than a 720p one.

> Single runs of this benchmark scatter widely on a busy host — one showed 0.8×,
> i.e. the larger frame appearing cheaper, which is not physically meaningful.
> The figures above are medians over 20 iterations. Prefer them to any single run.

Also verified: no `torch`, `tensorflow` or `ultralytics` in the environment;
detection explicitly targets CPU; the OCR engine is constructed lazily so a
counting-only deployment never pays for it; the UI needs no Node, bundler or
`node_modules`; the database is a single file.

**Not verified:** behaviour on real low-end hardware — a 2-core machine with 4 GB
RAM and slow storage. The headroom above suggests it will run at the `low`
profile, but that is an inference from a constrained simulation, not a
measurement.

## Results

- **[`TEST_REPORT.md`](TEST_REPORT.md)** — the full report: results, evidence,
  defects found, and what remains unverified
- `results/live_grid_check.json` — most recent live grid run
- `results/lightweight_benchmark.json` — most recent constrained benchmark
