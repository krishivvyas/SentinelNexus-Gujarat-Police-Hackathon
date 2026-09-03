"""Does it run on a low-end machine?

This host has 16 logical cores and 16.8 GB of RAM, which is a mid-range
development machine -- not the hardware the question is about. So rather than
claim a result the hardware cannot support, these tests **constrain the process**
to something a modest laptop would offer (2 OpenCV threads, the ``low`` profile)
and measure what the pipeline actually costs under that limit.

That is a simulation, not a substitute for running on real low-end hardware, and
the numbers are reported as such. What it does prove is that the work per frame
fits a small CPU budget and that nothing in the design assumes many cores or a
GPU.
"""
from __future__ import annotations

import gc
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import Settings

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "backend" / "data" / "ocr_samples"

#: Threads a low-end dual-core machine could realistically give OpenCV.
LOW_END_THREADS = 2


def _rss_mb() -> float:
    """Resident set size in MB, without adding a psutil dependency."""
    import ctypes
    import ctypes.wintypes as wt

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wt.HANDLE

    # Modern Windows exports this from kernel32 as K32GetProcessMemoryInfo;
    # psapi.dll still carries the original name. argtypes/restype must be
    # declared or the 64-bit handle and struct pointer are marshalled wrongly
    # and the call silently fails.
    for dll, name in ((kernel32, "K32GetProcessMemoryInfo"),
                      (ctypes.windll.psapi, "GetProcessMemoryInfo")):
        try:
            fn = getattr(dll, name)
        except (AttributeError, OSError):
            continue
        fn.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wt.DWORD]
        fn.restype = wt.BOOL

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if fn(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / 1e6
    return float("nan")


@pytest.fixture(scope="module")
def constrained():
    """Hold OpenCV to a low-end thread budget for the duration."""
    previous = cv2.getNumThreads()
    cv2.setNumThreads(LOW_END_THREADS)
    yield
    cv2.setNumThreads(previous)


@pytest.fixture(scope="module")
def frame():
    for candidate in sorted(SAMPLES.glob("*.jpg")):
        img = cv2.imread(str(candidate))
        if img is not None:
            return img
    # Fall back to a synthetic frame so the suite still runs on a clean clone.
    return np.random.default_rng(0).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)


# ------------------------------------------------------------------- profile

def test_low_profile_reduces_every_cost_lever():
    low = Settings(profile="low").apply_profile()
    balanced = Settings(profile="balanced").apply_profile()

    assert low.max_concurrent_streams == 2
    assert low.sample_interval_ms == 3000
    assert low.inference_width == 512

    # Each lever must move in the cheaper direction.
    assert low.max_concurrent_streams < balanced.max_concurrent_streams
    assert low.sample_interval_ms > balanced.sample_interval_ms
    assert low.inference_width < balanced.inference_width


def test_low_profile_processes_far_fewer_frames():
    """Frames decoded per camera per minute, by profile."""
    low = Settings(profile="low").apply_profile()
    balanced = Settings(profile="balanced").apply_profile()

    low_rate = 60_000 / low.sample_interval_ms * low.max_concurrent_streams
    bal_rate = 60_000 / balanced.sample_interval_ms * balanced.max_concurrent_streams
    assert low_rate <= bal_rate / 4, (
        f"low profile should cut the workload sharply: {low_rate} vs {bal_rate}")


# ------------------------------------------------------------------- runtime

def test_no_gpu_is_required():
    """The platform must not depend on CUDA, and must not import a GPU stack."""
    import importlib.util

    for heavy in ("torch", "tensorflow", "ultralytics"):
        assert importlib.util.find_spec(heavy) is None, (
            f"{heavy} must not be a dependency; inference runs on OpenCV DNN")


def test_detector_targets_cpu_explicitly():
    import inspect

    from app.pipeline import detect

    src = inspect.getsource(detect._model)
    assert "DNN_TARGET_CPU" in src
    assert "DNN_BACKEND_OPENCV" in src


@pytest.mark.slow
def test_detection_fits_a_low_end_cpu_budget(constrained, frame):
    """One frame of detection must be affordable at 2 threads.

    Two numbers matter and they differ by 3x. In a bare process detection costs
    ~233 ms per 1080p frame; with the whole application resident -- API, OCR,
    ORM, which is how it is actually deployed -- it costs ~680-775 ms. The
    second is the one to design against.
    """
    from app.pipeline.detect import VehicleDetector

    detector = VehicleDetector()
    detector.detect(frame)                      # warm the lazily-loaded net

    timings = []
    for _ in range(5):
        started = time.perf_counter()
        detector.detect(frame)
        timings.append((time.perf_counter() - started) * 1000)

    median = sorted(timings)[len(timings) // 2]
    print(f"\n  detection @ {LOW_END_THREADS} threads, "
          f"{frame.shape[1]}x{frame.shape[0]}: median {median:.0f} ms "
          f"(min {min(timings):.0f}, max {max(timings):.0f})")

    # 775 ms was measured with the whole application resident in this process,
    # which is the deployed condition. 1500 ms leaves room for a slower host
    # without letting a genuine regression through.
    assert median < 1500, f"detection too slow for a low-end host: {median:.0f} ms"


@pytest.mark.slow
def test_detection_keeps_up_with_the_low_profile_sampling_rate(constrained, frame):
    """Throughput must exceed what the low profile asks of it, with margin.

    The margin matters and was learned the hard way. Measured at 2 threads:

        bare process                       ~3.8 detections/s
        API server in another process      ~1.4 /s
        full application in-process        ~1.6-1.9 /s   <- the deployed case

    The profile was originally sized against the first number and could not
    sustain itself against the third. A 2x margin is asserted here so it cannot
    be tightened back to something that only works on an unloaded machine.
    """
    from app.pipeline.detect import VehicleDetector

    low = Settings(profile="low").apply_profile()
    required_per_second = (1000 / low.sample_interval_ms) * low.max_concurrent_streams

    detector = VehicleDetector()
    detector.detect(frame)

    started = time.perf_counter()
    runs = 6
    for _ in range(runs):
        detector.detect(frame)
    achievable = runs / (time.perf_counter() - started)

    print(f"\n  required {required_per_second:.1f} detections/s, "
          f"achievable {achievable:.1f}/s at {LOW_END_THREADS} threads "
          f"({achievable / required_per_second:.1f}x margin)")
    assert achievable >= required_per_second * 2.0, (
        f"low profile leaves too little margin for a loaded machine: "
        f"need {required_per_second * 2.0:.1f}/s to be safe, got {achievable:.1f}/s")


@pytest.mark.slow
def test_memory_footprint_is_modest(constrained, frame):
    """A 4 GB machine must not be exhausted by loading the pipeline."""
    from app.pipeline.detect import VehicleDetector

    gc.collect()
    before = _rss_mb()

    detector = VehicleDetector()
    for _ in range(3):
        detector.detect(frame)

    gc.collect()
    after = _rss_mb()
    print(f"\n  RSS {before:.0f} MB -> {after:.0f} MB "
          f"(detector adds ~{after - before:.0f} MB)")

    if after == after:                          # not NaN
        assert after < 1500, f"resident memory too high for a small machine: {after:.0f} MB"


@pytest.mark.slow
def test_downscaling_bounds_the_work_regardless_of_camera_resolution(constrained):
    """A 2560x1440 camera must not cost proportionally more than a 720p one.

    Frames are downscaled to the profile's inference width before the forward
    pass, so cost is bounded by the profile rather than by whatever resolution a
    department happens to have installed.
    """
    from app.pipeline.detect import VehicleDetector

    rng = np.random.default_rng(1)
    small = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    large = rng.integers(0, 255, (1440, 2560, 3), dtype=np.uint8)

    detector = VehicleDetector()
    detector.detect(small)

    def timed(img) -> float:
        started = time.perf_counter()
        for _ in range(3):
            detector.detect(img)
        return (time.perf_counter() - started) / 3 * 1000

    t_small, t_large = timed(small), timed(large)
    pixel_ratio = (2560 * 1440) / (1280 * 720)
    print(f"\n  720p {t_small:.0f} ms vs 1440p {t_large:.0f} ms "
          f"({pixel_ratio:.0f}x the pixels, {t_large / t_small:.1f}x the time)")

    assert t_large < t_small * pixel_ratio, (
        "inference cost must be bounded by the profile, not the camera")


@pytest.mark.slow
def test_ocr_is_not_loaded_until_a_plate_is_read():
    """The OCR engine is the heaviest component; it must be lazily constructed.

    A deployment that only does vehicle counting should never pay for it.
    """
    from app.pipeline import ocr

    ocr._ocr.cache_clear()
    assert ocr._ocr.cache_info().currsize == 0

    # Normalisation and validation must work without touching the engine.
    assert ocr.normalise("GJ01AB1234")[1] is True
    assert ocr.read_plate([]) .text == ""
    assert ocr._ocr.cache_info().currsize == 0, (
        "plate validation must not instantiate the OCR engine")


def test_no_build_step_is_required_for_the_ui():
    """The UI must not need Node, a bundler, or node_modules."""
    static = REPO / "backend" / "static" / "index.html"
    assert static.exists()
    assert not (REPO / "package.json").exists()
    assert not (REPO / "node_modules").exists()
    assert static.stat().st_size < 200_000, "the UI should stay a single small page"


def test_database_is_a_single_portable_file():
    settings = Settings()
    assert settings.database_url.startswith("sqlite"), (
        "the prototype must not require a database server")

