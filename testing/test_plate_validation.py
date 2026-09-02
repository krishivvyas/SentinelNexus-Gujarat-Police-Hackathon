"""Plate validation tests.

The regression these guard against is real: an earlier build reported the
camera's own burned-in date overlay ("14-06-2026") and street signage
("PALDI JUNCTION") as licence plates, because any text that vaguely fit the
shape was accepted. A traffic scene is full of text; only a genuine Indian
registration layout with a real state code may be reported as a plate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.pipeline.ocr import PlateVoter, PlateRead, normalise

# Every one of these was actually emitted as a "plate" by the earlier build.
JUNK = [
    "14-06-2026", "2026", "PALOLJUNCTIC", "PALOISUNCTIO", "O 6slyl sI5",
    ".t4", "0.05141", "1?", "gqa", "IA062026", "4-01", "y] s15", "I 513",
    "6s|yl s15", "phrtoorgpera.", "OG51Y1515",
]

REAL = ["GJ01AB1234", "MH12CD4567", "DL10CAB1234", "GJ05CD5678", "KA03MN9999"]


@pytest.mark.parametrize("text", JUNK)
def test_junk_is_not_a_plate(text):
    _, valid = normalise(text)
    assert valid is False, f"{text!r} must not validate as a plate"


@pytest.mark.parametrize("text", REAL)
def test_real_plates_validate(text):
    normalised, valid = normalise(text)
    assert valid is True, f"{text!r} should validate, got {normalised!r}"


def test_spacing_and_case_are_normalised():
    assert normalise("gj 05 cd 5678")[0] == "GJ05CD5678"
    assert normalise("GJ-01-AB-1234")[0] == "GJ01AB1234"


def test_unknown_state_code_is_rejected():
    # Correct layout, state code that does not exist.
    _, valid = normalise("ZZ01AB1234")
    assert valid is False


def test_ocr_confusions_are_repaired_position_aware():
    # O/0 and B/8 confusions in the slots where they are unambiguous.
    assert normalise("GJ0lAB1234")[0] == "GJ01AB1234"
    assert normalise("GJ01A81234")[0] == "GJ01AB1234"


def test_voter_prefers_valid_reads_over_confident_invalid_ones():
    voter = PlateVoter()
    for _ in range(3):
        voter.add(PlateRead(text="GJ01XX9999", confidence=0.95, valid=False))
    voter.add(PlateRead(text="GJ01AB1234", confidence=0.70, valid=True))
    voter.add(PlateRead(text="GJ01AB1234", confidence=0.70, valid=True))
    consensus, frames = voter.consensus()
    assert consensus.text == "GJ01AB1234"
    assert frames == 2


def test_voter_is_empty_without_reads():
    consensus, frames = PlateVoter().consensus()
    assert consensus.text == ""
    assert frames == 0
