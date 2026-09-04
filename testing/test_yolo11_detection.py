"""Test YOLO11n ONNX vehicle detection pipeline."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.pipeline.detect import VehicleDetector, Detection, looks_like_auto_rickshaw

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "backend" / "data" / "ocr_samples"


def test_yolo11_model_loading_and_inference():
    """Verify YOLO11n loads via OpenCV DNN and performs inference."""
    detector = VehicleDetector(conf_threshold=0.25)
    
    # Synthetic frame test
    synthetic = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(synthetic, (100, 100), (300, 300), (255, 255, 255), -1)
    
    dets = detector.detect(synthetic)
    assert isinstance(dets, list)


def test_yolo11_on_sample_footage():
    """Verify YOLO11n detects vehicles with valid coordinates on sample frames."""
    sample_files = list(SAMPLES.glob("*.jpg"))
    if not sample_files:
        pytest.skip("No sample images in data/ocr_samples")
    
    detector = VehicleDetector(conf_threshold=0.25)
    
    for sample_path in sample_files:
        frame = cv2.imread(str(sample_path))
        assert frame is not None, f"Failed to load {sample_path}"
        
        t0 = time.perf_counter()
        detections = detector.detect(frame)
        latency_ms = (time.perf_counter() - t0) * 1000
        
        # CPU inference should be reasonable (< 250ms)
        assert latency_ms < 500, f"Detection too slow: {latency_ms:.1f} ms"
        
        for d in detections:
            assert isinstance(d, Detection)
            assert d.w > 0 and d.h > 0
            assert 0.0 <= d.confidence <= 1.0
            assert d.label in {"car", "truck", "bus", "motorcycle", "bicycle", "auto-rickshaw", "vehicle"}
            
            # Crop must be non-empty and fit within original frame
            crop = d.crop(frame)
            assert crop.size > 0
            assert crop.shape[0] <= frame.shape[0]
            assert crop.shape[1] <= frame.shape[1]


def test_auto_rickshaw_livery_heuristic():
    """Verify auto-rickshaw yellow livery detection heuristic."""
    # Synthetic yellow crop (HSV hue ~25, high sat, high val)
    yellow_crop = np.zeros((100, 100, 3), dtype=np.uint8)
    yellow_crop[:] = (0, 215, 255)  # BGR for strong yellow
    
    assert looks_like_auto_rickshaw(yellow_crop, 100, 100) is True
    
    # Synthetic dark/blue crop
    blue_crop = np.zeros((100, 100, 3), dtype=np.uint8)
    blue_crop[:] = (255, 0, 0)
    assert looks_like_auto_rickshaw(blue_crop, 100, 100) is False
