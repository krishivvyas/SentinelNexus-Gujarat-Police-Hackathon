"""ONNX Runtime inference for the YOLO detection family.

Why a second backend at all
---------------------------
The original detector was YOLOv4-tiny through ``cv2.dnn``. It is fast, but it is
a 2020 model trained at 416 px, and this grid is its worst case: wide-angle night
PTZ overviews where a car occupies 40x30 px. It misses those outright.

YOLO11 is materially better on exactly that failure mode. It runs here through
ONNX Runtime rather than PyTorch for two reasons that both matter on this
deployment:

  * ``requirements.txt`` documents that Torch and PaddlePaddle fight over DLLs on
    Windows when both load into one process, and PaddleOCR is not optional -- it
    reads the burned-in overlay clock that every event timestamp comes from.
  * ONNX Runtime is a ~15 MB wheel with no CUDA payload. Torch is ~2.5 GB.

``cv2.dnn.readNetFromONNX`` would also load these graphs, but ONNX Runtime is
consistently faster on the same weights and has better operator coverage for the
YOLO11 head, so it is preferred and OpenCV stays as the fallback path.

Output layout
-------------
Every YOLOv8/v11-family export used here produces one tensor shaped
``(1, 4 + num_classes, num_anchors)`` -- ``(1, 84, 8400)`` for the 80-class COCO
models at 640 px, ``(1, 5, 8400)`` for the single-class plate detector. The first
four rows are ``cx, cy, w, h`` in *letterboxed input* pixels; the rest are
per-class scores, already activated. There is no objectness row (that is YOLOv5
and earlier) and NMS is not baked in, so it is applied here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("sentinel.onnx")

#: Grey used to pad a letterboxed frame. This is the Ultralytics training
#: convention; matching it avoids a distribution shift at the padded edges.
PAD_VALUE = 114


class OnnxUnavailable(RuntimeError):
    """onnxruntime or a weights file is missing.

    Callers treat this as "fall back to the previous backend", never as fatal.
    A missing optional model must not stop the platform from ingesting.
    """


@dataclass(slots=True)
class RawBox:
    """One post-NMS box in original-frame pixel coordinates."""

    x: int
    y: int
    w: int
    h: int
    class_id: int
    confidence: float


def letterbox(frame: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    """Resize preserving aspect ratio, pad to a square ``size x size``.

    Returns the padded image plus the scale and offsets needed to map boxes back.
    A plain ``cv2.resize`` to a square would squash a 16:9 traffic frame by 44%
    vertically, turning a car into a shape the model never saw in training --
    measurably worse on small vehicles, which is the entire problem on this grid.
    """
    height, width = frame.shape[:2]
    scale = min(size / width, size / height)
    new_w, new_h = max(1, int(round(width * scale))), max(1, int(round(height * scale)))

    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
    pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def _nms(rects: list[list[int]], scores: list[float], class_ids: list[int],
         conf_threshold: float, nms_threshold: float) -> list[int]:
    """Class-aware NMS, with a plain-NMS fallback on older OpenCV builds.

    Class-aware matters here: a "car" box and a "truck" box on the same pixels
    are a real ambiguity worth keeping, whereas two "car" boxes on the same
    pixels are not. ``NMSBoxesBatched`` arrived in OpenCV 4.7 -- if this is an
    older build, suppressing across classes is the safe degradation.
    """
    try:
        indices = cv2.dnn.NMSBoxesBatched(rects, scores, class_ids,
                                          conf_threshold, nms_threshold)
    except (AttributeError, cv2.error):
        indices = cv2.dnn.NMSBoxes(rects, scores, conf_threshold, nms_threshold)
    if indices is None or len(indices) == 0:
        return []
    return [int(i) for i in np.array(indices).flatten()]


class OnnxDetector:
    """A YOLOv8/v11-family ONNX graph, loaded once and reused.

    Thread safety: ``InferenceSession.run`` is thread-safe, and the live preview
    calls it from its detector thread while ingest workers call it from theirs.
    One shared session is therefore correct, and it avoids paying the (large)
    model load per worker.
    """

    def __init__(self, weights: Path, *, threads: int = 0,
                 conf_threshold: float = 0.25, nms_threshold: float = 0.45) -> None:
        self.weights = Path(weights)
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold

        if not self.weights.exists():
            raise OnnxUnavailable(f"weights missing: {self.weights}")

        try:
            import onnxruntime as ort
        except ImportError as exc:      # pragma: no cover - environment dependent
            raise OnnxUnavailable(f"onnxruntime not installed: {exc}") from exc

        options = ort.SessionOptions()
        # Leave one core for the decoder and the API. Threads are otherwise
        # oversubscribed against the FFmpeg reader threads and the whole process
        # gets slower, not faster.
        options.intra_op_num_threads = threads or max(1, (os.cpu_count() or 2) - 1)
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # ORT logs its own optimisation notices at warning level; the useful
        # signal is already in the log line below.
        options.log_severity_level = 3

        try:
            self.session = ort.InferenceSession(
                str(self.weights), options, providers=["CPUExecutionProvider"])
        except Exception as exc:
            raise OnnxUnavailable(f"failed to load {self.weights.name}: {exc}") from exc

        model_input = self.session.get_inputs()[0]
        self.input_name = model_input.name
        # These exports are fixed-size, but a dynamic axis reads back as a string
        # rather than an int, so fall back to the YOLO default rather than crash.
        shape = model_input.shape
        self.size = shape[2] if isinstance(shape[2], int) else 640

        output_shape = self.session.get_outputs()[0].shape
        self.num_classes = (output_shape[1] - 4) if isinstance(output_shape[1], int) else 80
        self.threads = options.intra_op_num_threads

        log.info("onnx detector %s loaded (%dx%d, %d classes, %d threads)",
                 self.weights.name, self.size, self.size, self.num_classes,
                 self.threads)

    # ---------------------------------------------------------------- inference

    def infer(self, frame: np.ndarray, *, classes: set[int] | None = None
              ) -> list[RawBox]:
        """Detect in one frame, returning boxes in original-frame coordinates."""
        if frame is None or frame.size == 0:
            return []

        padded, scale, pad_x, pad_y = letterbox(frame, self.size)
        # BGR->RGB, HWC->CHW, 0-255 -> 0-1, add the batch axis.
        blob = np.ascontiguousarray(
            padded[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0

        try:
            raw = self.session.run(None, {self.input_name: blob})[0]
        except Exception as exc:
            log.warning("onnx inference failed: %s", exc)
            return []

        return self._decode(raw, frame.shape[:2], scale, pad_x, pad_y, classes)

    def _decode(self, raw: np.ndarray, frame_shape: tuple[int, int], scale: float,
                pad_x: int, pad_y: int, classes: set[int] | None) -> list[RawBox]:
        """Turn the ``(1, 4+C, N)`` head into NMS'd boxes in frame coordinates."""
        # -> (N, 4+C): one row per anchor, which is what the vectorised filtering
        # below wants. The transpose is a view, not a copy.
        predictions = np.squeeze(raw, axis=0).T
        if predictions.size == 0:
            return []

        scores_all = predictions[:, 4:]

        # Restricting to the classes we want *before* the argmax is what keeps
        # this cheap. On a street scene most of the 8400 anchors are people,
        # traffic lights and street signs, and scoring those costs exactly as
        # much as scoring a car.
        if classes:
            wanted = np.array(sorted(classes), dtype=np.int32)
            wanted = wanted[wanted < scores_all.shape[1]]
            if wanted.size == 0:
                return []
            subset = scores_all[:, wanted]
            best_local = subset.argmax(axis=1)
            confidences = subset[np.arange(len(subset)), best_local]
            class_ids = wanted[best_local]
        else:
            class_ids = scores_all.argmax(axis=1)
            confidences = scores_all[np.arange(len(scores_all)), class_ids]

        keep = confidences >= self.conf_threshold
        if not keep.any():
            return []

        boxes = predictions[keep, :4]
        confidences = confidences[keep]
        class_ids = class_ids[keep]

        # cx,cy,w,h (letterboxed px) -> x,y,w,h (original frame px).
        half_w, half_h = boxes[:, 2] / 2.0, boxes[:, 3] / 2.0
        x = (boxes[:, 0] - half_w - pad_x) / scale
        y = (boxes[:, 1] - half_h - pad_y) / scale
        w = boxes[:, 2] / scale
        h = boxes[:, 3] / scale

        height, width = frame_shape
        # Clip to the frame. A box legitimately extends past the edge for a
        # vehicle half out of shot, and a negative origin makes every downstream
        # crop come back empty.
        x = np.clip(x, 0, width - 1)
        y = np.clip(y, 0, height - 1)
        w = np.clip(np.minimum(w, width - x), 0, None)
        h = np.clip(np.minimum(h, height - y), 0, None)

        rects = np.stack([x, y, w, h], axis=1).astype(np.int32)

        keep_indices = _nms(rects.tolist(),
                            confidences.astype(np.float32).tolist(),
                            class_ids.astype(np.int32).tolist(),
                            self.conf_threshold, self.nms_threshold)

        results: list[RawBox] = []
        for i in keep_indices:
            bx, by, bw, bh = (int(v) for v in rects[i])
            if bw <= 0 or bh <= 0:
                continue
            results.append(RawBox(x=bx, y=by, w=bw, h=bh,
                                  class_id=int(class_ids[i]),
                                  confidence=float(confidences[i])))
        return results
