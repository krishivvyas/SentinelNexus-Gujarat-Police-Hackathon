"""The grid's operating rules, turned into tests.

Each test names the rule it enforces. Several are static checks over the source
rather than behavioural ones -- for a rule like "no timing logic may depend on
CAP_PROP_FPS", proving the call site does not exist is stronger evidence than
observing correct output on one lucky run.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.config import Settings, settings
from helpers import code_only as _code_only
from app.pipeline import worker as worker_mod
from app.pipeline.worker import (LOOP_BACKWARD_MS, LOOP_FORWARD_MS, StreamWorker,
                                 detect_discontinuity)

BACKEND = Path(__file__).resolve().parent.parent / "backend"
APP_SOURCES = sorted((BACKEND / "app").rglob("*.py"))


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- DO: RTSP/TCP

def test_rtsp_transport_is_forced_to_tcp():
    """DO: force RTSP over TCP. UDP fails across NAT and yields corrupt frames."""
    import os

    opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    assert "rtsp_transport;tcp" in opts, (
        "OPENCV_FFMPEG_CAPTURE_OPTIONS must pin rtsp_transport;tcp "
        f"(got {opts!r})")


def test_transport_is_set_before_cv2_import():
    """The FFmpeg option is only read at import, so it must be set first."""
    src = _source(BACKEND / "app" / "pipeline" / "worker.py")
    env_pos = src.index("OPENCV_FFMPEG_CAPTURE_OPTIONS")
    cv2_pos = src.index("import cv2")
    assert env_pos < cv2_pos, "capture options must be set before 'import cv2'"


def test_udp_transport_is_never_requested():
    for path in APP_SOURCES:
        assert "rtsp_transport;udp" not in _source(path), f"UDP requested in {path.name}"


# ------------------------------------------------- DON'T: trust reported FPS

def test_no_timing_logic_uses_cap_prop_fps():
    """DON'T: trust the reported frame rate.

    This fleet reports 90000 fps (CAM-06) and 200 fps (CAM-30). Reading FPS as
    registry metadata is fine; using it in a calculation is not. Any arithmetic
    on CAP_PROP_FPS is a failure.
    """
    offenders: list[str] = []
    for path in APP_SOURCES:
        code = _code_only(path)
        if "CAP_PROP_FPS" not in code:
            continue
        for num, line in enumerate(_source(path).splitlines(), 1):
            stripped = line.split("#", 1)[0]
            if "CAP_PROP_FPS" not in stripped:
                continue
            # Permitted: plain capture into a variable for metadata.
            if re.search(r"^\s*fps\s*=\s*cap\.get\(cv2\.CAP_PROP_FPS\)\s*$", stripped):
                continue
            if re.search(r"CAP_PROP_FPS.*[-+*/]|[-+*/].*CAP_PROP_FPS", stripped):
                offenders.append(f"{path.name}:{num}: {line.strip()}")
    assert not offenders, "FPS used in arithmetic:\n" + "\n".join(offenders)


def test_frame_number_over_fps_pattern_is_absent():
    """The specific anti-pattern: frame_number / reported_fps."""
    pattern = re.compile(r"frame_(?:number|count|idx|index)\s*/\s*\w*fps", re.I)
    for path in APP_SOURCES:
        assert not pattern.search(_code_only(path)), f"frame/fps timing in {path.name}"


# ----------------------------------------------------------- DO: PTS timing

def test_worker_reads_pts_for_every_frame():
    src = inspect.getsource(StreamWorker.stream)
    assert "CAP_PROP_POS_MSEC" in src, "the worker must read the presentation timestamp"


def test_sampling_compares_pts_not_wall_clock():
    """DO: drive timing from PTS, never arrival time.

    A buffered GOP replays faster than real time on connect, so sampling on
    arrival time would silently over-sample the first seconds of every stream.
    """
    src = inspect.getsource(StreamWorker.stream)
    assert "pts - last_emitted_pts" in src, "sampler must difference PTS values"
    # time.time() must not appear in the sampling decision.
    sampling = src[src.index("last_emitted_pts is not None"):]
    sampling = sampling[:sampling.index("yield")]
    assert "time.time()" not in sampling, "sampling decision must not read wall clock"


def test_frame_event_exposes_pts_and_delta():
    from app.pipeline.worker import FrameEvent

    fields = FrameEvent.__dataclass_fields__
    for required in ("pts_ms", "pts_delta_ms", "discontinuity", "reconnected"):
        assert required in fields, f"FrameEvent must expose {required}"


# ------------------------------------ DON'T: assume a constant frame rate

@pytest.mark.parametrize("gap_ms", [500.0, 2_000.0, 8_720.0, 14_000.0, 30_000.0])
def test_inter_frame_gaps_are_not_discontinuities(gap_ms):
    """DON'T: treat a gap as a disconnect. 14 s was measured live on CAM-04."""
    assert detect_discontinuity(100_000.0, 100_000.0 + gap_ms) is False


def test_read_failure_grace_tolerates_a_run_of_empty_reads():
    w = StreamWorker("rtsp://example/none", camera_id="T")
    assert w.read_failure_grace >= 10, (
        "a handful of empty reads is normal here; the worker must not reconnect "
        "on the first one")


# ------------------------------------------------- DO: reconnect with backoff

def test_backoff_is_exponential_and_capped_at_30s():
    """DO: exponential backoff, ~2 s to a ~30 s cap."""
    w = StreamWorker("rtsp://example/none", camera_id="T")
    assert w.backoff_base == pytest.approx(2.0)
    assert w.backoff_cap == pytest.approx(30.0)

    delays = [min(w.backoff_base * (2 ** n), w.backoff_cap) for n in range(8)]
    assert delays[:4] == [2.0, 4.0, 8.0, 16.0]
    assert max(delays) <= 30.0
    assert delays == sorted(delays), "backoff must be non-decreasing"


def test_backoff_sleep_is_interruptible_not_a_tight_loop():
    """A tight retry loop would hammer the server; the wait must also be stoppable."""
    src = inspect.getsource(StreamWorker._connect_with_backoff)
    assert "_stop.wait(" in src, "backoff must use an interruptible Event.wait"
    assert "time.sleep" not in src, "backoff must not block on time.sleep"


def test_stop_breaks_the_reconnect_loop_immediately():
    w = StreamWorker("rtsp://127.0.0.1:9/none", camera_id="T",
                     backoff_base=30.0, backoff_cap=30.0, open_timeout_s=1.0)
    w.stop()
    assert w._connect_with_backoff() is None
    assert list(w.stream()) == []


# ------------------------------------- DON'T: treat decoder warnings as fatal

def test_decoder_warnings_are_logged_once_not_raised():
    src = inspect.getsource(StreamWorker.stream)
    assert "_warned_decoder" in src, "join-time decoder noise must be logged once"
    # The grab() call must be wrapped so a decode exception cannot kill the loop.
    assert "except Exception as exc:" in src


def test_ffmpeg_log_level_is_quietened():
    import os

    assert os.environ.get("OPENCV_FFMPEG_LOGLEVEL") == "-8", (
        "libavcodec logs a line per corrupt macroblock; that is background noise "
        "on this grid and must not flood the operator's logs")


# --------------------------------------------- DO: expect scene discontinuity

def test_loop_point_is_detected():
    """DO: each feed loops; PTS drops back to the start of the recording."""
    assert detect_discontinuity(600_000.0, 120.0) is True


def test_large_forward_jump_is_a_discontinuity():
    assert detect_discontinuity(1_000.0, 1_000.0 + LOOP_FORWARD_MS + 1) is True


def test_first_frame_is_never_a_discontinuity():
    assert detect_discontinuity(None, 987_654.0) is False


def test_discontinuity_boundaries_are_exact():
    assert detect_discontinuity(10_000.0, 10_000.0 - LOOP_BACKWARD_MS) is False
    assert detect_discontinuity(10_000.0, 10_000.0 - LOOP_BACKWARD_MS - 1) is True


def test_sampler_state_resets_at_a_discontinuity():
    src = inspect.getsource(StreamWorker.stream)
    assert "last_emitted_pts = None" in src, (
        "after a loop the sampler must restart, or the first frame of the new "
        "scene is dropped by an interval comparison against the old timeline")


def test_ingest_closes_tracks_across_a_cut():
    """Long-lived state must recover from a hard cut rather than span it."""
    from app.services.ingest import CameraIngest

    src = inspect.getsource(CameraIngest.run)
    assert "event.discontinuity" in src
    assert "tracker.reset()" in src, (
        "tracks must be closed at the loop point, otherwise a vehicle from "
        "before the cut is joined to one after it")


# --------------------------------------------------------- DO: pace the load

def test_concurrent_stream_opens_are_capped():
    """DO: each client gets its own stream copy, so open only what you process."""
    assert worker_mod._OPEN_SEMAPHORE is not None
    assert settings.max_concurrent_streams <= 8


def test_every_open_is_paired_with_a_close():
    src = inspect.getsource(StreamWorker)
    assert src.count("_OPEN_SEMAPHORE.acquire") == 1
    # Released on the failure paths inside _open, and in _close.
    assert src.count("_OPEN_SEMAPHORE.release") >= 3
    assert "finally:" in inspect.getsource(StreamWorker.stream)


def test_capture_is_released_even_if_the_consumer_raises():
    assert "finally:" in inspect.getsource(StreamWorker._close)
    assert "cap.release()" in inspect.getsource(StreamWorker._close)


def test_ingest_only_opens_triage_selected_cameras():
    from app.services.ingest import IngestManager

    src = inspect.getsource(IngestManager.start_ai_cameras)
    assert "ai_enabled" in src, "only cameras worth the CPU should be opened"


# ------------------------------------- DON'T: plan on downloading footage

def test_no_recorded_file_fallback_in_the_stream_path():
    """The grid is consumed live; there is no file download to fall back on."""
    src = _source(BACKEND / "app" / "pipeline" / "worker.py")
    assert ".mp4" not in src and ".avi" not in src


# ------------------------------------------------------- HLS fallback exists

def test_hls_connector_exists_and_supports_credentials():
    from app.connectors.hls import HLSConnector

    c = HLSConnector(cookie="session=x")
    assert c.protocol == "HLS"
    assert hasattr(c, "frames") and hasattr(c, "probe")
    assert "Cookie" in inspect.getsource(HLSConnector._apply_headers)


def test_worker_can_fall_back_to_hls_when_rtsp_is_blocked():
    src = inspect.getsource(StreamWorker._connect_with_backoff)
    assert "fallback_url" in src, (
        "if 8554 is blocked the worker must try the HLS endpoint")


# ------------------------------------------------------------ configuration

def test_profiles_scale_the_pipeline():
    low = Settings(profile="low").apply_profile()
    balanced = Settings(profile="balanced").apply_profile()
    high = Settings(profile="high").apply_profile()

    assert low.max_concurrent_streams < balanced.max_concurrent_streams < high.max_concurrent_streams
    assert low.sample_interval_ms > balanced.sample_interval_ms > high.sample_interval_ms
    assert low.inference_width < balanced.inference_width


def test_no_credentials_are_hardcoded():
    """Secrets come from the environment, never the source."""
    suspicious = re.compile(r"(password|secret|api_key)\s*=\s*[\"'](?!.*\{)"
                            r"(?!dev-only-change-in-production)"
                            r"(?!sentinel-)(?!\s*$)[^\"']{8,}[\"']", re.I)
    offenders = []
    for path in APP_SOURCES:
        for num, line in enumerate(_source(path).splitlines(), 1):
            if suspicious.search(line) and "getenv" not in line and "Field(" not in line:
                offenders.append(f"{path.name}:{num}")
    assert not offenders, f"possible hardcoded secret: {offenders}"
