"""Sentinel Nexus - One-Click Launcher & Environment Orchestrator.

This script:
1. Validates or creates the Python virtual environment (.venv-clean).
2. Installs any missing dependencies automatically.
3. Downloads the detector weights if missing (YOLO11 ONNX ~38 MB, plus the
   YOLOv4-tiny fallback ~24 MB).
4. Seeds the database (users, 30 cameras, watchlist) if not already initialized.
5. Launches the Sentinel Nexus server on http://localhost:8000 and opens the browser.

Usage:
    python run.py
    py run.py
"""
from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
MODELS_DIR = ROOT_DIR / "models"
VENV_DIR = ROOT_DIR / ".venv-clean"
DATA_DIR = BACKEND_DIR / "data"

if sys.platform == "win32":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"

# Dependencies are installed from the pinned requirements file, never from a
# bare-name list. The pins matter: an unpinned "numpy" resolves to 2.x, which
# mixes with the numpy-1.x compiled wheels PaddleOCR ships and breaks at import.
REQUIREMENTS_FILE = BACKEND_DIR / "requirements.txt"


YOLO_WEIGHTS_URL = "https://github.com/AlexeyAB/darknet/releases/download/yolov4/yolov4-tiny.weights"
YOLO_WEIGHTS_PATH = MODELS_DIR / "yolov4-tiny.weights"
YOLO_CFG_URL = "https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg"
YOLO_CFG_PATH = MODELS_DIR / "yolov4-tiny.cfg"

# YOLO11 ONNX exports -- the primary detector. Pre-exported so that nothing here
# needs PyTorch or the ultralytics package just to produce a .onnx file.
#
# (filename, url, approx MB, required)
# Only yolo11s is required: it is what the default "balanced" profile loads, and
# it measured best on this grid. The others are optional and the launcher does
# not fail without them -- detect.py degrades through the ladder to YOLOv4-tiny.
ONNX_BASE = "https://huggingface.co/giangndm/yolo11-onnx/resolve/main"
ONNX_MODELS = [
    ("yolo11s.onnx", f"{ONNX_BASE}/yolo11s_640.onnx", 38, True),
    ("yolo11n.onnx", f"{ONNX_BASE}/yolo11n_640.onnx", 11, False),
    ("yolo11m.onnx", f"{ONNX_BASE}/yolo11m_640.onnx", 80, False),
    # Learned plate localiser. Ships disabled -- it measured zero detections on
    # this grid's night footage -- but is fetched so that enabling it on better
    # cameras needs no network access. See pipeline/plate_detect.py.
    ("plate-detector.onnx",
     "https://huggingface.co/morsetechlab/yolov11-license-plate-detection/"
     "resolve/main/license-plate-finetune-v1n.onnx", 10, False),
]


def print_banner():
    banner = r"""
====================================================================
           SENTINEL NEXUS - GUJARAT POLICE SURVEILLANCE GRID         
               Unified CCTV Interoperability & Intelligence         
====================================================================
"""
    print(banner)


def check_and_create_venv():
    """Ensure .venv-clean exists."""
    if not VENV_PYTHON.exists():
        print(f"[*] Creating virtual environment at: {VENV_DIR} ...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        print("[+] Virtual environment created successfully.")


def reexec_in_venv():
    """Re-launch the script using the virtual environment's Python if not inside it."""
    is_in_venv = sys.prefix == str(VENV_DIR) or Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    if not is_in_venv:
        print(f"[*] Switching execution to virtual environment ({VENV_PYTHON}) ...\n")
        env = os.environ.copy()
        result = subprocess.run([str(VENV_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:], env=env)
        sys.exit(result.returncode)


def _pinned_requirements() -> list[tuple[str, str]]:
    """Parse ``name==version`` pins out of the requirements file.

    Extras are stripped -- "uvicorn[standard]==0.34.0" installs the distribution
    "uvicorn" -- and comment/blank/unpinned lines carry nothing to verify.
    """
    pins: list[tuple[str, str]] = []
    for raw in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, _, version = line.partition("==")
        pins.append((name.split("[", 1)[0].strip(), version.strip()))
    return pins


def _unsatisfied(pins: list[tuple[str, str]]) -> list[str]:
    """Which pins the current interpreter does not already satisfy.

    Checks installed distribution metadata rather than importability, so a
    package present at the wrong version -- numpy 2.x against the 1.26.4 pin --
    is reported instead of silently passing.
    """
    problems: list[str] = []
    for name, want in pins:
        try:
            have = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{name} (not installed)")
            continue
        if _normalise(have) != _normalise(want):
            problems.append(f"{name} {have} (pinned {want})")
    return problems


def _normalise(version: str) -> tuple:
    """Compare versions by numeric components, so "4.11.0.86" survives a round trip."""
    parts = []
    for chunk in version.split("."):
        parts.append(int(chunk) if chunk.isdigit() else chunk)
    return tuple(parts)


def check_and_install_deps():
    """Install the pinned dependencies, but only if they are not already satisfied."""
    print("[*] Checking dependencies ...")
    if not REQUIREMENTS_FILE.exists():
        print(f"[!] No requirements file at {REQUIREMENTS_FILE}.")
        sys.exit(1)

    pins = _pinned_requirements()
    problems = _unsatisfied(pins)
    if not problems:
        print(f"[+] All {len(pins)} pinned dependencies already satisfied.")
        return

    print(f"[*] {len(problems)} of {len(pins)} dependencies need installing:")
    for problem in problems:
        print(f"      - {problem}")
    print(f"[*] Installing from {REQUIREMENTS_FILE} (a few minutes on a fresh venv) ...")
    cmd = [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
    subprocess.run(cmd, check=True)

    remaining = _unsatisfied(pins)
    if remaining:
        # pip exited 0 but the tree still does not match -- a resolver backtrack
        # or a conflicting preinstalled package. Say so rather than booting into
        # a mismatched environment.
        print("[!] pip finished but these are still unsatisfied:")
        for problem in remaining:
            print(f"      - {problem}")
        sys.exit(1)
    print("[+] Packages installed successfully.")


def _download(url: str, path, label: str, min_bytes: int) -> bool:
    """Fetch one model file, writing to a temp name so a partial file is never
    left looking complete.

    A truncated .onnx is worse than a missing one: it loads far enough to fail
    with "Protobuf parsing failed" at the first frame, which reads as a code bug
    rather than a bad download.
    """
    if path.exists() and path.stat().st_size >= min_bytes:
        return True
    partial = path.with_suffix(path.suffix + ".part")
    try:
        print(f"[*] Downloading {label} ...")
        urllib.request.urlretrieve(url, partial)
        if partial.stat().st_size < min_bytes:
            raise OSError(f"got {partial.stat().st_size} bytes, "
                          f"expected at least {min_bytes}")
        partial.replace(path)
        print(f"[+] Saved: {path.name}")
        return True
    except Exception as exc:
        print(f"[!] Could not download {label}: {exc}")
        partial.unlink(missing_ok=True)
        return False


def check_and_download_models():
    """Ensure the detector weights exist.

    YOLO11 (ONNX Runtime) is the primary detector; YOLOv4-tiny stays as the
    fallback so the platform still detects if the ONNX fetch fails on a
    restricted network.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not YOLO_CFG_PATH.exists():
        print("[*] Downloading YOLOv4-tiny config ...")
        urllib.request.urlretrieve(YOLO_CFG_URL, YOLO_CFG_PATH)
        print(f"[+] Saved: {YOLO_CFG_PATH}")

    _download(YOLO_WEIGHTS_URL, YOLO_WEIGHTS_PATH,
              "YOLOv4-tiny weights (~24 MB, fallback detector)", 1_000_000)

    missing_required = []
    for filename, url, megabytes, required in ONNX_MODELS:
        ok = _download(url, MODELS_DIR / filename,
                       f"{filename} (~{megabytes} MB)",
                       int(megabytes * 0.9 * 1024 * 1024))
        if not ok and required:
            missing_required.append(filename)

    if missing_required:
        print("[!] Primary YOLO11 weights are missing: "
              f"{', '.join(missing_required)}")
        print("    Detection will fall back to YOLOv4-tiny, which finds "
              "roughly half as many vehicles on this grid's night footage.")
    else:
        print("[+] Detector weights ready (YOLO11 + YOLOv4-tiny fallback).")


def check_and_seed_db():
    """Ensure database exists and is seeded with cameras & users."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "evidence").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "thumbnails").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "own_feed").mkdir(parents=True, exist_ok=True)

    db_file = DATA_DIR / "sentinel.db"
    if not db_file.exists():
        print("[*] Initializing camera registry & database ...")
        script = BACKEND_DIR / "scripts" / "seed_registry.py"
        subprocess.run([str(VENV_PYTHON), "-m", "scripts.seed_registry", "--ai-top", "8"],
                       cwd=str(BACKEND_DIR), check=True)
        print("[+] Database seeded with 30 cameras and default accounts.")
    else:
        print("[+] Database ready.")


def open_browser_later():
    """Wait 2 seconds and open http://localhost:8000 in default browser."""
    import threading

    def _open():
        time.sleep(2)
        print("\n[*] Opening Command Centre in your default browser...")
        try:
            webbrowser.open("http://localhost:8000")
        except Exception:
            pass

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def run_server():
    """Start uvicorn server."""
    print("\n" + "=" * 68)
    print("  [>] SENTINEL NEXUS IS READY")
    print("  [>] Web Command Centre : http://localhost:8000")
    print("  [>] Swagger API Docs   : http://localhost:8000/docs")
    print("  [>] Credentials:")
    print("      - Admin    : admin    / sentinel-admin")
    print("      - Operator : operator / sentinel-operator")
    print("      - Analyst  : analyst  / sentinel-analyst")
    print("  [>] Press CTRL + C to stop the server anytime.")
    print("=" * 68 + "\n")

    open_browser_later()

    import uvicorn
    # Add backend directory to sys.path so 'app.main:app' imports work
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    os.chdir(str(BACKEND_DIR))
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")


def main():
    print_banner()
    check_and_create_venv()
    reexec_in_venv()
    check_and_install_deps()
    check_and_download_models()
    check_and_seed_db()
    run_server()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Sentinel Nexus server stopped cleanly.")
