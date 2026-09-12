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


CORE_MODULES = [
    ("fastapi", "fastapi>=0.115.6"),
    ("uvicorn", "uvicorn[standard]>=0.34.0"),
    ("sqlalchemy", "sqlalchemy>=2.0.36"),
    ("pydantic", "pydantic>=2.10.4"),
    ("pydantic_settings", "pydantic-settings>=2.7.0"),
    ("jose", "python-jose[cryptography]>=3.3.0"),
    ("bcrypt", "bcrypt>=4.2.1"),
    ("multipart", "python-multipart>=0.0.20"),
    ("httpx", "httpx>=0.28.1"),
    ("reportlab", "reportlab>=4.2.5"),
    ("numpy", "numpy>=1.26.4"),
    ("cv2", "opencv-contrib-python>=4.10.0"),
    ("onnxruntime", "onnxruntime>=1.20.1"),
    ("paddle", "paddlepaddle>=3.0.0"),
    ("paddleocr", "paddleocr>=2.9.1"),
    ("setuptools", "setuptools>=70.0.0"),
]


def check_and_install_deps():
    """Ensure all required dependencies are installed and importable."""
    print("[*] Checking dependencies ...")
    missing_packages = []
    for mod_name, pkg_spec in CORE_MODULES:
        try:
            __import__(mod_name)
        except ImportError:
            missing_packages.append(pkg_spec)

    if not missing_packages:
        print("[+] All core dependencies satisfied.")
        return

    print(f"[*] Installing {len(missing_packages)} missing packages: {', '.join(missing_packages)} ...")
    cmd = [str(VENV_PYTHON), "-m", "pip", "install"] + missing_packages
    subprocess.run(cmd, check=True)
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


FRONTEND_DIR = ROOT_DIR / "frontend"

frontend_process: subprocess.Popen | None = None


def start_frontend() -> bool:
    """Launch Next.js Ultra UI dev server in background if Node/npm are available."""
    global frontend_process
    if not FRONTEND_DIR.exists():
        return False

    import shutil
    npm_bin = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm_bin:
        print("[!] Node.js / npm not found on PATH. Next.js frontend won't auto-launch.")
        print("    (You can install Node.js v18+ to run the Next.js Ultra UI).")
        return False

    # Check node_modules
    if not (FRONTEND_DIR / "node_modules").exists():
        print("[*] Installing Next.js frontend dependencies (first time only) ...")
        try:
            subprocess.run([npm_bin, "install"], cwd=str(FRONTEND_DIR), check=True)
            print("[+] Frontend dependencies installed.")
        except Exception as e:
            print(f"[!] Failed to install frontend dependencies: {e}")
            return False

    print("[*] Starting Next.js Ultra UI frontend on http://localhost:3000 ...")
    try:
        frontend_process = subprocess.Popen(
            [npm_bin, "run", "dev"],
            cwd=str(FRONTEND_DIR),
            shell=sys.platform == "win32",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(f"[!] Could not start Next.js frontend: {e}")
        return False


def open_browser_later(has_frontend: bool = True):
    """Wait 2.5 seconds and open the Command Centre in default browser."""
    import threading

    target_url = "http://localhost:3000" if has_frontend else "http://localhost:8000"

    def _open():
        time.sleep(2.5)
        print(f"\n[*] Opening Command Centre ({target_url}) in your default browser...")
        try:
            webbrowser.open(target_url)
        except Exception:
            pass

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def run_server():
    """Start uvicorn server and Next.js frontend."""
    has_frontend = start_frontend()

    print("\n" + "=" * 68)
    print("  [>] SENTINEL NEXUS IS READY")
    if has_frontend:
        print("  [>] Next.js Ultra UI  : http://localhost:3000 (Recommended)")
    print("  [>] Backend & API     : http://localhost:8000")
    print("  [>] Swagger API Docs  : http://localhost:8000/docs")
    print("  [>] Credentials:")
    print("      - Admin    : admin    / sentinel-admin")
    print("      - Operator : operator / sentinel-operator")
    print("      - Analyst  : analyst  / sentinel-analyst")
    print("  [>] Press CTRL + C to stop the entire grid anytime.")
    print("=" * 68 + "\n")

    open_browser_later(has_frontend)

    import uvicorn
    # Add backend directory to sys.path so 'app.main:app' imports work
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    os.chdir(str(BACKEND_DIR))
    try:
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
    finally:
        if frontend_process and frontend_process.poll() is None:
            print("\n[*] Stopping Next.js frontend...")
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=3)
            except Exception:
                frontend_process.kill()


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
