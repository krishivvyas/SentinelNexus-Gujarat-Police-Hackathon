"""Timing-rule tests for the stream worker.

These cover the rules that are easy to get wrong and expensive to debug live:
a loop point must be detected, and an ordinary inter-frame gap must never be
mistaken for one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline.worker import LOOP_BACKWARD_MS, LOOP_FORWARD_MS, detect_discontinuity


def test_first_frame_is_never_a_discontinuity():
    assert detect_discontinuity(None, 0.0) is False
    assert detect_discontinuity(None, 98765.0) is False


def test_normal_advance_is_not_a_discontinuity():
    assert detect_discontinuity(1000.0, 1040.0) is False          # 25 fps step


def test_inter_frame_gaps_are_tolerated():
    # Gaps of several seconds are normal here; 14 s was measured on CAM-04.
    for gap in (500.0, 2_000.0, 8_720.0, 14_000.0, 30_000.0):
        assert detect_discontinuity(100_000.0, 100_000.0 + gap) is False, gap


def test_loop_point_is_detected():
    # The feed loops: PTS drops from late in the recording back to the start.
    assert detect_discontinuity(600_000.0, 120.0) is True


def test_small_backward_jitter_is_not_a_loop():
    assert detect_discontinuity(5_000.0, 5_000.0 - LOOP_BACKWARD_MS / 2) is False


def test_large_forward_jump_is_a_discontinuity():
    assert detect_discontinuity(1_000.0, 1_000.0 + LOOP_FORWARD_MS + 1) is True


def test_boundaries_are_exact():
    assert detect_discontinuity(10_000.0, 10_000.0 - LOOP_BACKWARD_MS) is False
    assert detect_discontinuity(10_000.0, 10_000.0 - LOOP_BACKWARD_MS - 1) is True
