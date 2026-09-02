"""Shared test configuration.

Puts ``backend/`` on the import path so tests can import ``app.*`` without the
package being installed, and defines the markers used to separate fast offline
tests from ones that touch the live camera grid.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"

sys.path.insert(0, str(BACKEND))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: needs network access to the camera grid (slow)")
    config.addinivalue_line(
        "markers", "slow: takes more than a few seconds")
