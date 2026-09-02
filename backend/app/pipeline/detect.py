"""Vehicle detection on OpenCV's DNN module.

Inference runs entirely inside OpenCV (``cv2.dnn``). There is no PyTorch,
TensorFlow or ultralytics dependency -- the model is a weights file that
``cv2.dnn.readNetFromDarknet`` loads, and OpenCV does the forward pass on CPU.

Measured: ~160 ms per 1920x1080 frame on a CPU-only machine at 416x416 input.
Frames are downscaled to the profile's inference width before the forward pass
and boxes are mapped back to full-resolution coordinates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..config import MODELS_DIR, settings

log = logging.getLogger("sentinel.detect")

CFG = MODELS_DIR / "yolov4-tiny.cfg"
WEIGHTS = MODELS_DIR / "yolov4-tiny.weights"

# COCO ids we care about. "bicycle" is kept because two-wheelers dominate this
# traffic and are frequently confused with motorcycles at night.
VEHICLE_CLASSES: dict[int, str] = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}
PERSON_CLASS = 0

# Named colours in HSV. Hue is degrees/2 as OpenCV stores it (0-179).
_COLOUR_BANDS: list[tuple[str, int, int]] = [
    ("red", 0, 10), ("orange", 11, 22), ("yellow", 23, 33),
    ("green", 34, 85), ("cyan", 86, 95), ("blue", 96, 130),
    ("purple", 131, 158), ("red", 159, 179),
]


@dataclass(slots=True)
class Detection:
    """One detected vehicle in full-resolution frame coordinates."""

    x: int
    y: int
    w: int
    h: int
    label: str
    confidence: float
    colour: str = ""

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    def crop(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        x0, y0 = max(0, self.x), max(0, self.y)
        x1, y1 = min(w, self.x + self.w), min(h, self.y + self.h)
        return frame[y0:y1, x0:x1]

    def to_dict(self) -> dict:
        return {"bbox": f"{self.x},{self.y},{self.w},{self.h}", "label": self.label,
                "confidence": round(self.confidence, 3), "colour": self.colour}


def estimate_colour(crop: np.ndarray) -> str:
    """Best-effort dominant colour of a vehicle crop.

    Samples the middle of the box to avoid road and background, and separates
    achromatic paint (white/grey/black, which is most of this fleet) from hue
    before naming a colour.
    """
    if crop is None or crop.size == 0:
        return ""
    h, w = crop.shape[:2]
    if h < 8 or w < 8:
        return ""

    # Central patch: body panels rather than windows, shadow or road.
    patch = crop[int(h * 0.35):int(h * 0.75), int(w * 0.25):int(w * 0.75)]
    if patch.size == 0:
        patch = crop

    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hue, sat, val = (hsv[..., i].astype(np.float32) for i in range(3))

    mean_sat, mean_val = float(sat.mean()), float(val.mean())

    # Low saturation means the paint has no hue worth naming.
    if mean_sat < 45:
        if mean_val < 55:
            return "black"
        if mean_val > 175:
            return "white"
        return "grey"

    # Weight hues by how saturated and bright each pixel is.
    mask = (sat > 60) & (val > 45)
    if not mask.any():
        return "grey"
    dominant = float(np.median(hue[mask]))
    for name, low, high in _COLOUR_BANDS:
        if low <= dominant <= high:
            return name
    return "grey"


@lru_cache(maxsize=1)
def _model() -> cv2.dnn_DetectionModel:
    """Load the detector once. Constructed lazily so importing is cheap."""
    if not CFG.exists() or not WEIGHTS.exists():
        raise FileNotFoundError(
            f"detector weights missing: expected {CFG} and {WEIGHTS}. "
            "See README 'Install' for the download step."
        )
    net = cv2.dnn.readNetFromDarknet(str(CFG), str(WEIGHTS))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    model = cv2.dnn_DetectionModel(net)
    size = int(settings.inference_width)
    # Darknet expects a multiple of 32.
    size = max(320, (size // 32) * 32)
    model.setInputParams(size=(size, size), scale=1 / 255.0, swapRB=True)
    log.info("detector loaded (OpenCV DNN, %dx%d input)", size, size)
    return model


class VehicleDetector:
    """Detects vehicles in a frame and describes them."""

    def __init__(self, conf_threshold: float = 0.3, nms_threshold: float = 0.45,
                 min_area_ratio: float = 0.00015) -> None:
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        # Drop specks: anything smaller than this fraction of the frame carries
        # no usable plate or colour information.
        self.min_area_ratio = min_area_ratio

    def detect(self, frame: np.ndarray, *, with_colour: bool = True) -> list[Detection]:
        if frame is None or frame.size == 0:
            return []

        model = _model()
        try:
            class_ids, confidences, boxes = model.detect(
                frame, self.conf_threshold, self.nms_threshold
            )
        except cv2.error as exc:
            log.warning("detection failed: %s", exc)
            return []

        if len(class_ids) == 0:
            return []

        frame_area = frame.shape[0] * frame.shape[1]
        results: list[Detection] = []

        for cid, conf, box in zip(np.array(class_ids).flatten(),
                                  np.array(confidences).flatten(),
                                  np.array(boxes).reshape(-1, 4)):
            label = VEHICLE_CLASSES.get(int(cid))
            if label is None:
                continue
            x, y, w, h = (int(v) for v in box)
            if w <= 0 or h <= 0 or (w * h) / frame_area < self.min_area_ratio:
                continue

            det = Detection(x=x, y=y, w=w, h=h, label=label, confidence=float(conf))
            if with_colour:
                det.colour = estimate_colour(det.crop(frame))
            results.append(det)

        # Largest first: the nearest vehicle is the one most likely to yield a plate.
        results.sort(key=lambda d: d.area, reverse=True)
        return results


def draw(frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
    """Annotate a copy of the frame -- used for evidence snapshots."""
    out = frame.copy()
    for d in detections:
        cv2.rectangle(out, (d.x, d.y), (d.x + d.w, d.y + d.h), (0, 220, 0), 2)
        tag = f"{d.label} {d.confidence:.2f}"
        if d.colour:
            tag = f"{d.colour} {tag}"
        cv2.putText(out, tag, (d.x, max(14, d.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1, cv2.LINE_AA)
    return out
