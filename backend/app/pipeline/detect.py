"""Vehicle detection.

Two backends behind one interface, selected at load time:

  * **YOLO11 on ONNX Runtime** (default). A 2024 detector, run from a plain
    ``.onnx`` file with no PyTorch and no ultralytics package. This is what
    finds the small, dim, distant vehicles that this grid is full of.
  * **YOLOv4-tiny on ``cv2.dnn``** (fallback). The original detector, kept
    because OpenCV is already a hard dependency, so the platform still detects
    when onnxruntime is absent or a weights download has not happened yet.

Measured on this 16-core CPU-only host, per 1920x1080 frame, all threads:

    yolo11n  640   ~50 ms      lightest, "low" profile
    yolo11s  640   ~78 ms      default, "balanced" profile
    yolo11m  640  ~201 ms      "high" profile
    yolov4-tiny    ~160 ms     fallback, and the weakest of the four on small
                               vehicles despite the middling cost

Everything above this module -- the ingest worker, the live preview, the
evidence writer -- consumes ``Detection`` and never learns which backend ran.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..config import MODELS_DIR, settings
from .onnx_backend import OnnxDetector, OnnxUnavailable

log = logging.getLogger("sentinel.detect")

CFG = MODELS_DIR / "yolov4-tiny.cfg"
WEIGHTS = MODELS_DIR / "yolov4-tiny.weights"

#: ONNX weights per model name. All are COCO-trained, so the class ids below
#: apply unchanged whichever one is loaded.
ONNX_WEIGHTS: dict[str, Path] = {
    "yolo11n": MODELS_DIR / "yolo11n.onnx",
    "yolo11s": MODELS_DIR / "yolo11s.onnx",
    "yolo11m": MODELS_DIR / "yolo11m.onnx",
    "yolo11l": MODELS_DIR / "yolo11l.onnx",
}

# COCO ids we care about. "bicycle" is kept because two-wheelers dominate this
# traffic and are frequently confused with motorcycles at night.
VEHICLE_CLASSES: dict[int, str] = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    # COCO has no auto-rickshaw, and every model here is COCO-trained, so autos
    # and small tempos land wherever they fit -- most often "car" or "truck",
    # and verified on this grid also on class 56 ("chair"). On a road-facing
    # camera a chair detection is a vehicle, so it is kept under a generic
    # label rather than discarded; the auto-rickshaw test renames it when it can.
    56: "vehicle",
}

#: Auto-rickshaw livery across Gujarat is a strong yellow body. In OpenCV's
#: 0-179 hue scale that is roughly 18-38, measured on this fleet.
AUTO_HUE_LOW, AUTO_HUE_HIGH = 18, 38
#: Fraction of body pixels that must be that yellow before a box is called an
#: auto. Set from measurement: a lit auto reads ~0.14, a red bus and a black van
#: read ~0.00, so this is deliberately well clear of both.
AUTO_YELLOW_FRACTION = 0.06
#: Autos are roughly as wide as they are tall; cars and trucks are wider.
AUTO_ASPECT_LOW, AUTO_ASPECT_HIGH = 0.65, 1.45
#: Only these labels are reconsidered as autos. A bus is never an auto, and a
#: two-wheeler misread as one would lose a real distinction.
AUTO_CANDIDATE_LABELS = frozenset({"car", "truck", "vehicle"})
PERSON_CLASS = 0

# Vehicle colour is deliberately not derived. This grid records overwhelmingly
# at night, where sodium and LED street lighting plus headlight bloom drive the
# apparent hue more than the paint does, so a named colour describes the
# illuminant. On a record an operator may act on, a confident wrong colour is
# worse than no colour, so none is produced, stored or displayed.


@dataclass(slots=True)
class Detection:
    """One detected vehicle in full-resolution frame coordinates."""

    x: int
    y: int
    w: int
    h: int
    label: str
    confidence: float

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
                "confidence": round(self.confidence, 3)}


def _body_patch(crop: np.ndarray) -> np.ndarray:
    """The body-panel band of a vehicle crop, used by the auto-rickshaw test.

    Deliberately below the vertical centre: on anything larger than a car the
    middle of the box is glass rather than bodywork. Panels sit in the lower
    half, inset horizontally to avoid the road and background either side.
    """
    h, w = crop.shape[:2]
    patch = crop[int(h * 0.45):int(h * 0.90), int(w * 0.15):int(w * 0.85)]
    return patch if patch.size else crop


def looks_like_auto_rickshaw(crop: np.ndarray, width: int, height: int) -> bool:
    """True when a box has the yellow body and squat shape of an auto-rickshaw.

    A heuristic, not a classifier: no COCO detector can name autos at all, so
    this recovers the common case (a lit yellow auto) from whatever class it
    landed on. It is deliberately conservative -- an unlit auto in shadow stays
    under its original label rather than risking a wrong one on a police record.
    """
    if crop is None or crop.size == 0 or height <= 0:
        return False
    if not AUTO_ASPECT_LOW <= width / height <= AUTO_ASPECT_HIGH:
        return False

    hsv = cv2.cvtColor(_body_patch(crop), cv2.COLOR_BGR2HSV)
    hue, sat, val = (hsv[..., i] for i in range(3))
    # No upper brightness bound: a lit auto's yellow body is genuinely bright,
    # and excluding bloom-level pixels drops its yellow fraction from 0.14 to
    # 0.03. Headlight glare is washed out rather than saturated, so the sat>70
    # floor already keeps it out.
    paint = (sat > 70) & (val > 50)
    if not paint.any():
        return False
    yellow = paint & (hue >= AUTO_HUE_LOW) & (hue <= AUTO_HUE_HIGH)
    return float(yellow.sum()) / float(paint.size) >= AUTO_YELLOW_FRACTION


# --------------------------------------------------------------------- backends

@lru_cache(maxsize=1)
def _darknet_model() -> cv2.dnn_DetectionModel:
    """Load YOLOv4-tiny once. Constructed lazily so importing stays cheap."""
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
    log.info("detector loaded (OpenCV DNN, YOLOv4-tiny, %dx%d input)", size, size)
    return model


@lru_cache(maxsize=1)
def _onnx_model() -> OnnxDetector | None:
    """Load the configured YOLO11 graph, or return None to fall back.

    Returning None rather than raising is deliberate: a missing optional model
    must degrade the platform to the older detector, never stop ingestion. The
    reason is logged once at load, so an operator can see which one is running.
    """
    name = (settings.detector or "auto").strip().lower()
    if name in {"yolov4-tiny", "darknet", "opencv"}:
        return None

    candidates = [name] if name in ONNX_WEIGHTS else []
    if name == "auto":
        # Preferred model for the hardware profile first, then progressively
        # lighter ones, so a partial download still yields a working detector.
        # Ordered by measured recall on this grid rather than by parameter
        # count -- see PROFILE_PRESETS for why yolo11m ranks below yolo11n here.
        preferred = settings.profile_detector()
        order = ["yolo11s", "yolo11n", "yolo11m", "yolo11l"]
        candidates = [preferred] + [m for m in order if m != preferred]

    for candidate in candidates:
        try:
            return OnnxDetector(
                ONNX_WEIGHTS[candidate],
                threads=settings.detector_threads,
                conf_threshold=settings.detector_conf,
                nms_threshold=settings.detector_nms,
            )
        except OnnxUnavailable as exc:
            log.info("detector %s unavailable (%s)", candidate, exc)

    log.warning("no YOLO11 weights loaded; falling back to YOLOv4-tiny. "
                "Run 'python run.py' to fetch the ONNX models.")
    return None


def active_backend() -> dict[str, object]:
    """Describe the detector actually in use -- surfaced in /api/stats.

    An operator reading a detection count needs to know which model produced it,
    especially when a fallback silently took over.
    """
    model = _onnx_model()
    if model is not None:
        return {"runtime": "onnxruntime", "model": model.weights.stem,
                "input": model.size, "threads": model.threads,
                "conf": settings.detector_conf}
    return {"runtime": "opencv-dnn", "model": "yolov4-tiny",
            "input": int(settings.inference_width), "threads": None,
            "conf": settings.detector_conf}


class VehicleDetector:
    """Detects vehicles in a frame and describes them.

    The backend is chosen once per process and shared; constructing a detector
    per worker is cheap and does not reload weights.
    """

    def __init__(self, conf_threshold: float | None = None,
                 nms_threshold: float = 0.45,
                 min_area_ratio: float = 0.00015) -> None:
        self.conf_threshold = (settings.detector_conf if conf_threshold is None
                               else conf_threshold)
        self.nms_threshold = nms_threshold
        # Drop specks: anything smaller than this fraction of the frame carries
        # no usable plate information.
        self.min_area_ratio = min_area_ratio

    # ------------------------------------------------------------------ raw pass

    def _raw(self, frame: np.ndarray) -> list[tuple[int, float, tuple[int, int, int, int]]]:
        """Run whichever backend is active, returning (class_id, conf, xywh)."""
        model = _onnx_model()
        if model is not None:
            return [(b.class_id, b.confidence, (b.x, b.y, b.w, b.h))
                    for b in model.infer(frame, classes=set(VEHICLE_CLASSES))]

        try:
            class_ids, confidences, boxes = _darknet_model().detect(
                frame, self.conf_threshold, self.nms_threshold)
        except cv2.error as exc:
            log.warning("detection failed: %s", exc)
            return []
        if len(class_ids) == 0:
            return []
        return [(int(cid), float(conf), tuple(int(v) for v in box))
                for cid, conf, box in zip(np.array(class_ids).flatten(),
                                          np.array(confidences).flatten(),
                                          np.array(boxes).reshape(-1, 4))]

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detect and label vehicles in one frame."""
        if frame is None or frame.size == 0:
            return []

        frame_area = frame.shape[0] * frame.shape[1]
        results: list[Detection] = []

        for class_id, confidence, (x, y, w, h) in self._raw(frame):
            label = VEHICLE_CLASSES.get(class_id)
            if label is None:
                continue
            if w <= 0 or h <= 0 or (w * h) / frame_area < self.min_area_ratio:
                continue
            if confidence < self.conf_threshold:
                continue

            det = Detection(x=x, y=y, w=w, h=h, label=label, confidence=confidence)
            crop = det.crop(frame)

            # Autos are not a COCO class, so recover them from whatever they
            # were labelled. Only the classes an auto is actually mistaken for
            # are considered -- a bus or a two-wheeler is never reconsidered.
            if label in AUTO_CANDIDATE_LABELS and looks_like_auto_rickshaw(crop, w, h):
                det.label = "auto-rickshaw"

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
        cv2.putText(out, tag, (d.x, max(14, d.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1, cv2.LINE_AA)
    return out
