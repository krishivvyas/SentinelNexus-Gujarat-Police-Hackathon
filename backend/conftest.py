"""Put the backend package root on sys.path so tests can ``import app``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
