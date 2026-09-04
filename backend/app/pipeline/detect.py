"""Vehicle detection on OpenCV's DNN module (YOLO11n ONNX / YOLOv4-tiny).

Inference runs entirely inside OpenCV (``cv2.dnn``) on CPU. There is no runtime
PyTorch, TensorFlow or GPU dependency -- the model is an ONNX weights file that
``cv2.dnn.readNetFromONNX`` loads, and OpenCV does the forward pass on CPU.

Default: YOLO11n ONNX (~10.2 MB, 640x640 input, anchor-free, high accuracy).
Fallback: YOLOv4-tiny Darknet (~24 MB).
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

YOLO11_ONNX = MODELS_DIR / "yolo11n.onnx"
DARKNET_CFG = MODELS_DIR / "yolov4-tiny.cfg"
DARKNET_WEIGHTS = MODELS_DIR / "yolov4-tiny.weights"

# COCO ids we care about. "bicycle" is kept because two-wheelers dominate this
# traffic and are frequently confused with motorcycles at night.
VEHICLE_CLASSES: dict[int, str] = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    # COCO has no auto-rickshaw, and the weights are COCO-trained, so autos and
    # small tempos land wherever they fit -- most often "car" or "truck", and
    # verified on this grid also on class 56 ("chair"). On a road-facing camera
    # a chair detection is a vehicle, so it is kept under a generic label rather
    # than discarded; reclassify_auto_rickshaw() renames it when it can.
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
    """The body-panel band of a vehicle crop, used by the auto-rickshaw test."""
    h, w = crop.shape[:2]
    patch = crop[int(h * 0.45):int(h * 0.90), int(w * 0.15):int(w * 0.85)]
    return patch if patch.size else crop


def looks_like_auto_rickshaw(crop: np.ndarray, width: int, height: int) -> bool:
    """True when a box has the yellow body and squat shape of an auto-rickshaw."""
    if crop is None or crop.size == 0 or height <= 0:
        return False
    if not AUTO_ASPECT_LOW <= width / height <= AUTO_ASPECT_HIGH:
        return False

    hsv = cv2.cvtColor(_body_patch(crop), cv2.COLOR_BGR2HSV)
    hue, sat, val = (hsv[..., i] for i in range(3))
    paint = (sat > 70) & (val > 50)
    if not paint.any():
        return False
    yellow = paint & (hue >= AUTO_HUE_LOW) & (hue <= AUTO_HUE_HIGH)
    return float(yellow.sum()) / float(paint.size) >= AUTO_YELLOW_FRACTION


@lru_cache(maxsize=1)
def _model() -> tuple[str, any]:
    """Load detector network targeting OpenCV DNN on CPU (YOLO11 ONNX / YOLOv4 fallback)."""
    if YOLO11_ONNX.exists():
        net = cv2.dnn.readNetFromONNX(str(YOLO11_ONNX))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        log.info("detector loaded (YOLO11n ONNX CPU, %s)", YOLO11_ONNX.name)
        return "onnx_yolo11", net

    if DARKNET_CFG.exists() and DARKNET_WEIGHTS.exists():
        net = cv2.dnn.readNetFromDarknet(str(DARKNET_CFG), str(DARKNET_WEIGHTS))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        model = cv2.dnn_DetectionModel(net)
        size = int(settings.inference_width)
        size = max(320, (size // 32) * 32)
        model.setInputParams(size=(size, size), scale=1 / 255.0, swapRB=True)
        log.info("detector loaded (YOLOv4-tiny Darknet fallback, %dx%d)", size, size)
        return "darknet", model

    raise FileNotFoundError(
        f"detector weights missing: expected {YOLO11_ONNX} or ({DARKNET_CFG} and {DARKNET_WEIGHTS}). "
        "Run python run.py to download model weights automatically."
    )


def _detector_backend() -> tuple[str, any]:
    """Alias for _model()."""
    return _model()


class VehicleDetector:
    """Detects vehicles in a frame and describes them."""

    def __init__(self, conf_threshold: float = 0.25, nms_threshold: float = 0.45,
                 min_area_ratio: float = 0.00015) -> None:
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.min_area_ratio = min_area_ratio
        self.target_cids = list(VEHICLE_CLASSES.keys())

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detect and label vehicles in one frame."""
        if frame is None or frame.size == 0:
            return []

        backend_type, net = _detector_backend()

        if backend_type == "onnx_yolo11":
            return self._detect_yolo11(frame, net)
        else:
            return self._detect_darknet(frame, net)

    def _detect_yolo11(self, frame: np.ndarray, net: cv2.dnn.Net) -> list[Detection]:
        """YOLO11 ONNX inference and output decoding."""
        h_orig, w_orig = frame.shape[:2]
        input_size = 640

        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (input_size, input_size), swapRB=True, crop=False)
        net.setInput(blob)
        try:
            output = net.forward()
        except cv2.error as exc:
            log.warning("YOLO11 inference failed: %s", exc)
            return []

        # YOLO11 ONNX output shape is (1, 84, N) -> transpose to (N, 84)
        if len(output.shape) != 3 or output.shape[1] < 4:
            return []

        preds = output[0].T
        boxes_raw = preds[:, :4]
        scores_raw = preds[:, 4:]

        # Filter target vehicle classes
        target_scores = scores_raw[:, self.target_cids]
        max_idx = np.argmax(target_scores, axis=1)
        max_scores = target_scores[np.arange(len(preds)), max_idx]
        matched_cids = np.array(self.target_cids)[max_idx]

        mask = max_scores >= self.conf_threshold
        if not mask.any():
            return []

        kept_boxes_raw = boxes_raw[mask]
        kept_scores = max_scores[mask]
        kept_cids = matched_cids[mask]

        # Rescale boxes to original frame dimensions
        x_scale = w_orig / float(input_size)
        y_scale = h_orig / float(input_size)

        candidate_boxes = []
        for (cx, cy, w, h) in kept_boxes_raw:
            x = int((cx - w / 2.0) * x_scale)
            y = int((cy - h / 2.0) * y_scale)
            bw = int(w * x_scale)
            bh = int(h * y_scale)
            candidate_boxes.append([x, y, bw, bh])

        indices = cv2.dnn.NMSBoxes(candidate_boxes, kept_scores.tolist(), self.conf_threshold, self.nms_threshold)
        if len(indices) == 0:
            return []

        frame_area = h_orig * w_orig
        results: list[Detection] = []

        for idx in indices:
            i = idx if isinstance(idx, (int, np.integer)) else idx[0]
            box = candidate_boxes[i]
            x, y, w, h = box
            if w <= 0 or h <= 0 or (w * h) / frame_area < self.min_area_ratio:
                continue

            cid = int(kept_cids[i])
            label = VEHICLE_CLASSES.get(cid, "vehicle")
            conf = float(kept_scores[i])

            det = Detection(x=x, y=y, w=w, h=h, label=label, confidence=conf)
            crop = det.crop(frame)

            # Auto-rickshaw livery recovery
            if label in AUTO_CANDIDATE_LABELS and looks_like_auto_rickshaw(crop, w, h):
                det.label = "auto-rickshaw"

            results.append(det)

        results.sort(key=lambda d: d.area, reverse=True)
        return results

    def _detect_darknet(self, frame: np.ndarray, model: cv2.dnn_DetectionModel) -> list[Detection]:
        """Darknet fallback inference."""
        try:
            class_ids, confidences, boxes = model.detect(
                frame, self.conf_threshold, self.nms_threshold
            )
        except cv2.error as exc:
            log.warning("Darknet detection failed: %s", exc)
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
            crop = det.crop(frame)

            if label in AUTO_CANDIDATE_LABELS and looks_like_auto_rickshaw(crop, w, h):
                det.label = "auto-rickshaw"

            results.append(det)

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
