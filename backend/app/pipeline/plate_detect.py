"""Learned licence-plate localisation.

What changed and why
--------------------
``plate.py`` locates plates with classical morphology -- blackhat, Sobel, close,
contour filter. That was the right first call: it costs nothing and the limiting
factor on this footage is pixels on the glyphs, not knowing roughly where the
plate sits.

It is still wrong often enough to matter. Morphology keys on "a horizontal band
of vertical strokes", and at night a lit grille, a rear light cluster, a bumper
reflector strip and a shop sign behind the vehicle all produce that. Each false
candidate costs a full OCR pass and, worse, can win the vote with garbage.

A single-class YOLO plate detector answers the same question directly, and this
module runs one -- ~10 MB, ~39 ms on a 640 px crop, applied only to vehicle
boxes already large enough to be worth reading.

Measured on this grid, it does not help
---------------------------------------
Over 55 vehicle crops sampled from this grid's own evidence store, the learned
detector produced **zero** detections. Localisation results were byte-identical
to morphology alone, for an extra 39 ms per crop:

    morphology only        22/55 crops yielded a candidate, 36 candidates,  1 ms/crop
    learned + morphology   22/55 crops yielded a candidate, 36 candidates, 39 ms/crop

Raw head scores explain why: ~0.002 on night crops, peaking at 0.16 on the
brightest frames in the store -- never within reach of any usable threshold.
Two different public plate models (a YOLOv8 and a YOLO11n fine-tune) agree.
These are wide-angle night overview PTZ cameras where a plate spans 20-40 px
with motion blur and headlight bloom; there is nothing for the model to lock
onto. That is the same conclusion ``plate.py`` reached before this was written,
now with numbers behind it.

So it ships **disabled** (``SENTINEL_PLATE_DETECTOR=1`` turns it on). The path
is kept because it is correct and cheap to enable: point this platform at a
dedicated ANPR camera or at daylight footage -- exactly the deployment this
would be scaled to -- and it becomes the better localiser immediately. What is
not acceptable is shipping a 39 ms/crop cost that buys nothing measurable.

Morphology is never replaced regardless. The two are complementary: when the
learned model does fire it is far more precise, and the classical pass still
finds plates at angles the model was not trained on. Candidates from both are
merged, learned ones ranked first.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import MODELS_DIR, settings
from .onnx_backend import OnnxDetector, OnnxUnavailable

log = logging.getLogger("sentinel.plate_detect")

PLATE_WEIGHTS = MODELS_DIR / "plate-detector.onnx"

#: A vehicle box smaller than this cannot hold a readable plate, so it is not
#: worth an inference pass. At 96 px wide a plate is ~20 px -- already at the
#: bottom of what the recovery chain can rescue.
MIN_VEHICLE_WIDTH = 96

#: Plate boxes are grown slightly before cropping. The detector is trained to
#: bound the plate tightly, and OCR does better with a few pixels of quiet zone
#: around the glyphs than with the outermost stroke clipped.
PAD_RATIO = 0.08


@lru_cache(maxsize=1)
def _model() -> OnnxDetector | None:
    """Load the plate detector once, or return None to use morphology alone."""
    if not settings.plate_detector_enabled:
        return None
    try:
        return OnnxDetector(PLATE_WEIGHTS,
                            threads=settings.detector_threads,
                            conf_threshold=settings.plate_detector_conf,
                            nms_threshold=0.4)
    except OnnxUnavailable as exc:
        log.info("learned plate detector unavailable (%s); "
                 "using morphological localisation only", exc)
        return None


def available() -> bool:
    """True when the learned detector loaded. Reported in /api/stats."""
    return _model() is not None


def find_plates(vehicle_crop: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    """Locate plates inside one vehicle crop.

    Returns ``(x, y, w, h, confidence)`` in crop coordinates, best first. An
    empty list means "this model found nothing", which is a real answer on this
    footage and not an error -- most night-time vehicles here genuinely have no
    legible plate.
    """
    model = _model()
    if model is None or vehicle_crop is None or vehicle_crop.size == 0:
        return []

    height, width = vehicle_crop.shape[:2]
    if width < MIN_VEHICLE_WIDTH:
        return []

    boxes = model.infer(vehicle_crop)
    results: list[tuple[int, int, int, int, float]] = []

    for box in boxes:
        pad_x = int(box.w * PAD_RATIO)
        pad_y = int(box.h * PAD_RATIO)
        x = max(0, box.x - pad_x)
        y = max(0, box.y - pad_y)
        w = min(width - x, box.w + 2 * pad_x)
        h = min(height - y, box.h + 2 * pad_y)
        if w <= 0 or h <= 0:
            continue
        # A "plate" filling most of the vehicle box is the detector latching
        # onto the whole rear of a truck. Reject rather than hand OCR a picture
        # of a lorry.
        if w > width * 0.9 and h > height * 0.6:
            continue
        results.append((x, y, w, h, box.confidence))

    results.sort(key=lambda r: r[4], reverse=True)
    return results


def describe() -> dict[str, object]:
    """Backend description for /api/stats."""
    model = _model()
    if model is None:
        note = ("disabled: measured zero detections on this grid's night "
                "footage; set SENTINEL_PLATE_DETECTOR=1 to enable")
        if settings.plate_detector_enabled:
            note = "enabled but weights failed to load; morphology only"
        return {"backend": "morphology", "model": None, "note": note}
    return {"backend": "onnxruntime", "model": model.weights.stem,
            "input": model.size, "conf": settings.plate_detector_conf,
            "note": "morphological candidates still merged in as a fallback"}
