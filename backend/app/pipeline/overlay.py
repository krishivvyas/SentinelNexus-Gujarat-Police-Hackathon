"""Burned-in overlay reader.

Every camera in the Sentinel fleet renders a wall-clock timestamp into the video,
and most also render a site name. That overlay is the authoritative event time --
far better than server wall-clock, because these are replayed recordings whose
content time has nothing to do with when we decoded them.

Reading it also tells us which cameras cover overlapping time windows, which is
what makes (or breaks) cross-camera correlation on this feed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

# Matches the overlay formats observed across the fleet:
#   13-06-2026 23:10:22        14-06-2026 06:42:48 AM
#   13/06/2026 23:11:44 Sat    2026-08-03 22:28:37
# The separator between date and time may be absent entirely: some cameras render
# "14-06-202602:46:23AM" with no gap at all, so the separator is \D{0,3}.
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})\D{0,3}(\d{1,2}):(\d{2}):(\d{2})"), "ymd"),
    (re.compile(r"(\d{2})[-/](\d{2})[-/](\d{4})\D{0,3}(\d{1,2}):(\d{2}):(\d{2})"), "dmy"),
]
_AMPM = re.compile(r"\b([AP])\.?M\.?\b", re.I)

# OCR confusions that matter inside a digit run.
_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0",
                            "I": "1", "l": "1", "|": "1",
                            "S": "5", "s": "5", "B": "8", "Z": "2", "G": "6"})


@dataclass
class OverlayRead:
    timestamp: datetime | None = None
    timestamp_text: str = ""
    site_name: str = ""
    raw_lines: list[str] = None
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.raw_lines is None:
            self.raw_lines = []


@lru_cache(maxsize=1)
def _ocr():
    """PaddleOCR is loaded once and reused; construction is expensive."""
    from paddleocr import PaddleOCR

    return PaddleOCR(use_angle_cls=False, lang="en", show_log=False)


def _strip(frame: np.ndarray, where: str) -> np.ndarray:
    h = frame.shape[0]
    band = max(64, h // 8)
    return frame[:band] if where == "top" else frame[h - band:]


def _prep(img: np.ndarray) -> np.ndarray:
    """Upscale small crops only.

    Measured on the fleet: contrast-stretching the band (CLAHE/threshold) makes
    the detector WORSE, because overlay glyphs are already high-contrast white on
    a darker scene and the stretch amplifies the background into competing edges.
    The raw crop is the best input; we only enlarge when it is small.
    """
    if img.shape[1] < 1000:
        img = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    return img


def _normalise_digits(text: str) -> str:
    """Fix letter/digit confusions only inside runs that look like date-time fields."""
    def fix(match: re.Match[str]) -> str:
        return match.group(0).translate(_DIGIT_FIX)

    return re.sub(r"[\dOoQDIl|SsBZG]{2,4}(?=[-/: ])|(?<=[-/: ])[\dOoQDIl|SsBZG]{2,4}", fix, text)


def parse_timestamp(text: str) -> tuple[datetime | None, str]:
    """Pull a datetime out of an OCR line, tolerating the fleet's format spread."""
    cleaned = _normalise_digits(text)
    is_pm = False
    m = _AMPM.search(cleaned)
    if m:
        is_pm = m.group(1).upper() == "P"

    for pattern, order in _DATE_PATTERNS:
        found = pattern.search(cleaned)
        if not found:
            continue
        g = [int(x) for x in found.groups()]
        if order == "ymd":
            year, month, day, hour, minute, second = g
        else:
            day, month, year, hour, minute, second = g

        if m:                                   # 12-hour clock
            if is_pm and hour < 12:
                hour += 12
            elif not is_pm and hour == 12:
                hour = 0

        try:
            return datetime(year, month, day, hour, minute, second), found.group(0)
        except ValueError:
            continue
    return None, ""


def _looks_like_site(text: str) -> bool:
    """A site label is mostly letters and is not the timestamp itself."""
    letters = sum(c.isalpha() for c in text)
    return letters >= 4 and letters / max(len(text), 1) > 0.45


def read_overlay(frame: np.ndarray, *, want_site: bool = True) -> OverlayRead:
    """OCR the top and bottom overlay bands of a frame."""
    ocr = _ocr()
    result = OverlayRead()
    candidates: list[tuple[str, float]] = []
    # Per-band detections kept in reading order, so a date and a time that were
    # detected as two neighbouring boxes can be rejoined.
    bands: list[list[tuple[str, float]]] = []

    for where in ("top", "bottom"):
        band_img = _prep(_strip(frame, where))
        try:
            raw = ocr.ocr(band_img, cls=False)
        except Exception:
            continue
        if not raw or not raw[0]:
            continue

        entries = []
        for entry in raw[0]:
            box, (text, conf) = entry[0], entry[1]
            x = min(pt[0] for pt in box)
            entries.append((x, text, float(conf)))
        entries.sort(key=lambda e: e[0])

        ordered = [(t, c) for _, t, c in entries]
        bands.append(ordered)
        candidates.extend(ordered)
        result.raw_lines.extend(t for t, _ in ordered)

    best_conf = 0.0
    for text, conf in candidates:
        ts, ts_text = parse_timestamp(text)
        if ts and conf > best_conf:
            result.timestamp, result.timestamp_text, best_conf = ts, ts_text, conf

    # Several cameras split the date and the time into separate detections
    # (e.g. "13-06-2026" + "23:24:10"). Rejoin neighbours within a band.
    if result.timestamp is None:
        for ordered in bands:
            for i in range(len(ordered) - 1):
                joined = f"{ordered[i][0]} {ordered[i + 1][0]}"
                ts, ts_text = parse_timestamp(joined)
                if ts:
                    result.timestamp, result.timestamp_text = ts, ts_text
                    best_conf = min(ordered[i][1], ordered[i + 1][1])
                    break
            if result.timestamp:
                break

    result.confidence = round(best_conf, 3)

    if want_site:
        site = [t for t, c in candidates
                if _looks_like_site(t) and t not in result.timestamp_text and c > 0.6]
        site = [s for s in site if not parse_timestamp(s)[0]]
        if site:
            result.site_name = max(site, key=len).strip()

    return result


def read_overlay_from_path(path: str) -> OverlayRead:
    frame = cv2.imread(path)
    if frame is None:
        return OverlayRead()
    return read_overlay(frame)
