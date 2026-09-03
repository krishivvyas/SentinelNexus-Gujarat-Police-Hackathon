"""Sentinel Nexus - One-Click Launcher & Environment Orchestrator.

This script:
1. Validates or creates the Python virtual environment (.venv-clean).
2. Installs any missing dependencies automatically.
3. Downloads YOLOv4-tiny weights if missing (~24 MB).
4. Seeds the database (users, 30 cameras, watchlist) if not already initialized.
5. Launches the Sentinel Nexus server on http://localhost:8000 and opens the browser.

Usage:
    python run.py
    py run.py
"""
from __future__ import annotations

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
    VENV_PIP = VENV_DIR / "Scripts" / "pip.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"
    VENV_PIP = VENV_DIR / "bin" / "pip"

REQUIRED_PACKAGES = [
    "fastapi",
    "uvicorn[standard]",
    "sqlalchemy",
    "pydantic",
    "pydantic-settings",
    "python-jose[cryptography]",
    "bcrypt",
    "python-multipart",
    "httpx",
    "reportlab",
    "numpy",
    "opencv-contrib-python",
    "paddlepaddle",
    "paddleocr",
]

YOLO_WEIGHTS_URL = "https://github.com/AlexeyAB/darknet/releases/download/yolov4/yolov4-tiny.weights"
YOLO_WEIGHTS_PATH = MODELS_DIR / "yolov4-tiny.weights"
YOLO_CFG_URL = "https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg"
YOLO_CFG_PATH = MODELS_DIR / "yolov4-tiny.cfg"


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


def check_and_install_deps():
    """Ensure core packages are installed in the venv."""
    print("[*] Checking dependencies ...")
    try:
        import fastapi
        import uvicorn
        import cv2
        import sqlalchemy
        print("[+] Core dependencies already satisfied.")
    except ImportError:
        print("[*] Installing required packages into virtual environment ...")
        cmd = [str(VENV_PIP), "install"] + REQUIRED_PACKAGES
        subprocess.run(cmd, check=True)
        print("[+] Packages installed successfully.")


def check_and_download_models():
    """Ensure YOLOv4-tiny weights and config exist."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not YOLO_CFG_PATH.exists():
        print(f"[*] Downloading YOLOv4-tiny config ...")
        urllib.request.urlretrieve(YOLO_CFG_URL, YOLO_CFG_PATH)
        print(f"[+] Saved: {YOLO_CFG_PATH}")

    if not YOLO_WEIGHTS_PATH.exists() or YOLO_WEIGHTS_PATH.stat().st_size < 1000000:
        print(f"[*] Downloading YOLOv4-tiny weights (~24 MB) ...")
        urllib.request.urlretrieve(YOLO_WEIGHTS_URL, YOLO_WEIGHTS_PATH)
        print(f"[+] Downloaded: {YOLO_WEIGHTS_PATH}")
    else:
        print("[+] YOLO model weights ready.")


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
