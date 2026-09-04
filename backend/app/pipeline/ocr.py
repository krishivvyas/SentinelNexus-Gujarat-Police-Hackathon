"""Plate OCR, normalisation and multi-frame voting.

Reads the restored plate crops with PaddleOCR, normalises the result to the
Indian registration format, and -- crucially on this footage -- votes across
several frames of the same vehicle. Reading one plate from ten blurry frames is
far more reliable than reading it from the single best frame.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

# Indian registration: two-letter state, one-or-two-digit RTO, one-to-three
# letter series, four digits. e.g. GJ01AB1234, GJ1A1234, DL10CAB1234
PLATE_RE = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$")

VALID_STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
    "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
    "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK",
    "UP", "WB",
}

# Confusions that matter, applied position-aware: a glyph read as "0" in a
# letter slot is almost always "O", and vice versa in a digit slot.
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                           "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7",
                           "A": "4"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z",
                           "6": "G", "4": "A"})


@dataclass
class PlateRead:
    text: str = ""
    raw: str = ""
    confidence: float = 0.0
    valid: bool = False

    def __bool__(self) -> bool:
        return bool(self.text)


@lru_cache(maxsize=1)
def _ocr():
    """PaddleOCR, constructed once on first use so import stays cheap."""
    import warnings

    warnings.filterwarnings("ignore")
    from paddleocr import PaddleOCR

    return PaddleOCR(use_angle_cls=False, lang="en", show_log=False)


def _clean(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _valid_rto(rto: str) -> bool:
    """RTO district codes run 01-99; a bare or all-zero code is not real.

    This matters for disambiguation as well as validity. "GJ0LAB1234" otherwise
    parses as RTO "0" with series "LAB" and is accepted as-is, when the reading
    the layout actually intends is RTO "01" with series "AB" -- the "L" being a
    misread "1".
    """
    return bool(rto) and rto.isdigit() and int(rto) > 0


# A generic fallback layout for footage outside India. Used only when the
# platform is configured for a non-Indian region; the Sentinel grid runs "IN".
GENERIC_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9]{5,10}$")


def normalise(raw: str, region: str | None = None) -> tuple[str, bool]:
    """Coerce an OCR string toward a valid plate for the configured region.

    Returns ``(normalised, is_valid)``. For "IN" (the default, and what the
    Sentinel grid uses) normalisation is position-aware: the layout tells us
    which glyphs must be letters and which must be digits, so an "O" in the last
    four characters becomes "0" and never the reverse.

    "GENERIC" accepts any mixed letter-and-digit string of plausible plate
    length. It exists so the platform can be demonstrated on non-Indian footage
    without weakening the Indian validation that guards the live grid.
    """
    text = _clean(raw)

    if (region or _region()) == "GENERIC":
        return text, bool(GENERIC_RE.match(text))

    if not (8 <= len(text) <= 11):
        # Too short or too long to be a full registration. A layout match still
        # has to carry a real state code, otherwise "IA062026" (the camera's own
        # date overlay) would pass as a plate.
        match = PLATE_RE.match(text)
        if match and text[:2] in VALID_STATE_CODES and _valid_rto(match.group(2)):
            return text, True
        return text, False

    match = PLATE_RE.match(text)
    if match and text[:2] in VALID_STATE_CODES and _valid_rto(match.group(2)):
        return text, True

    # Rebuild by position: AA DD LLL DDDD, working from both ends.
    state = text[:2].translate(_TO_ALPHA)
    tail = text[-4:].translate(_TO_DIGIT)
    middle = text[2:-4]

    # The RTO number is the leading digits of the middle section.
    rto_chars: list[str] = []
    idx = 0
    while idx < len(middle) and len(rto_chars) < 2:
        ch = middle[idx].translate(_TO_DIGIT)
        if ch.isdigit():
            rto_chars.append(ch)
            idx += 1
        else:
            break
    series = middle[idx:].translate(_TO_ALPHA)

    rto = "".join(rto_chars)
    candidate = f"{state}{rto}{series}{tail}"
    rebuilt = PLATE_RE.match(candidate)
    valid = bool(rebuilt) and state in VALID_STATE_CODES and _valid_rto(rto)
    return candidate, valid


def _region() -> str:
    from ..config import settings

    return (settings.plate_region or "IN").upper()


def _text_pieces(ocr, image: np.ndarray) -> list[tuple[str, float]]:
    """Every text fragment in one plate crop, as ``(text, confidence)``.

    Two passes, because PaddleOCR's text *detector* is tuned for finding text
    somewhere in a scene, not for an image that is already nothing but a plate.
    On a small crop it routinely returns no box at all, and recognition then
    never runs -- measured on this grid, detection+recognition produced text on
    0 of 62 plate crops while recognition alone produced text on all 62.

    So: try detection+recognition first, since it correctly splits a two-row
    plate into its state and number rows. When it finds nothing, fall back to
    running recognition over the whole crop as a single text line, which is
    exactly what the crop is. The fallback is deliberately unguarded about
    quality -- on a plate too small to read it returns single-character noise at
    0.1-0.5 confidence, and the confidence floor and layout validation in
    read_plate() reject that, as they are meant to.
    """
    try:
        result = ocr.ocr(image, cls=False)
    except Exception:
        result = None
    if result and result[0]:
        return [(entry[1][0], float(entry[1][1])) for entry in result[0]]

    try:
        result = ocr.ocr(image, det=False, cls=False)
    except Exception:
        return []
    if not result or not result[0]:
        return []
    return [(text, float(conf)) for text, conf in result[0] if text]


def read_plate(variants: list[np.ndarray], *, min_confidence: float = 0.55,
               require_valid: bool = True, region: str | None = None) -> PlateRead:
    """OCR several restorations of one plate and keep the best read.

    ``require_valid`` is on by default and is the difference between an ANPR
    system and a text detector. Traffic scenes are full of text -- the camera's
    own burned-in timestamp, shop hoardings, road signs -- and every one of those
    will happily OCR. Only a string that matches the Indian registration layout
    *and* carries a real state code is returned as a plate. Anything else yields
    an empty read, and the sighting is recorded on its attributes alone.
    """
    if not variants:
        return PlateRead()

    ocr = _ocr()
    best = PlateRead()

    for image in variants:
        if image is None or image.size == 0:
            continue
        pieces = _text_pieces(ocr, image)
        if not pieces:
            continue

        # A plate may be detected as two boxes (state row + number row).
        joined = "".join(p for p, _ in pieces)
        mean_conf = sum(c for _, c in pieces) / len(pieces)

        for raw, conf in ([(joined, mean_conf)] if len(pieces) > 1 else []) + pieces:
            if conf < min_confidence:
                continue
            text, valid = normalise(raw, region)
            if not text:
                continue
            if require_valid and not valid:
                continue
            if conf > best.confidence or (valid and not best.valid):
                best = PlateRead(text=text, raw=raw, confidence=round(conf, 3),
                                 valid=valid)

    return best


@dataclass
class PlateVoter:
    """Accumulates reads of one tracked vehicle and returns the consensus.

    Weighting favours layout-valid reads, so a single crisp valid read outvotes
    several confident-but-malformed ones.
    """

    votes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    best_raw: dict[str, str] = field(default_factory=dict)

    def add(self, read: PlateRead) -> None:
        if not read or not read.text:
            return
        weight = read.confidence * (2.5 if read.valid else 1.0)
        self.votes[read.text] += weight
        self.counts[read.text] += 1
        self.best_raw.setdefault(read.text, read.raw)

    def consensus(self) -> tuple[PlateRead, int]:
        """Return the winning read and how many frames contributed to it."""
        if not self.votes:
            return PlateRead(), 0
        text = max(self.votes, key=self.votes.get)
        frames = self.counts[text]
        normalised, valid = normalise(text)
        # Confidence reported is the mean weight per contributing frame.
        confidence = min(self.votes[text] / max(frames, 1), 1.0)
        return PlateRead(text=normalised, raw=self.best_raw.get(text, text),
                         confidence=round(confidence, 3), valid=valid), frames

    @property
    def total_reads(self) -> int:
        return sum(self.counts.values())
