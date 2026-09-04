"""The recognition-only fallback in the plate OCR chain.

PaddleOCR's text detector looks for text somewhere in a scene. A plate crop is
not a scene -- it is already nothing but text -- and on small crops the detector
routinely returns no box at all, so recognition never runs. Measured on this
grid: detection+recognition produced text on 0 of 62 plate crops where
recognition alone produced text on all 62.
"""
import numpy as np
import pytest

from app.pipeline.ocr import _text_pieces

IMAGE = np.zeros((40, 120, 3), dtype=np.uint8)

# PaddleOCR shapes: det+rec is [[[box, (text, conf)], ...]];
# rec-only is [[(text, conf), ...]].
DET_HIT = [[[[[0, 0], [1, 0], [1, 1], [0, 1]], ("GJ01AB1234", 0.93)]]]
REC_HIT = [[("GJ01AB1234", 0.71)]]


class FakeOCR:
    """Stands in for PaddleOCR, recording which pass was used."""

    def __init__(self, det_result=None, rec_result=None,
                 det_raises=False, rec_raises=False):
        self.det_result, self.rec_result = det_result, rec_result
        self.det_raises, self.rec_raises = det_raises, rec_raises
        self.calls: list[str] = []

    def ocr(self, image, det=True, cls=False):
        if det is False:
            self.calls.append("rec")
            if self.rec_raises:
                raise RuntimeError("recognition blew up")
            return self.rec_result
        self.calls.append("det")
        if self.det_raises:
            raise RuntimeError("detection blew up")
        return self.det_result


def test_detection_result_is_used_and_fallback_is_not_run():
    """When the detector finds text, it wins -- it splits a two-row plate."""
    ocr = FakeOCR(det_result=DET_HIT)
    assert _text_pieces(ocr, IMAGE) == [("GJ01AB1234", 0.93)]
    assert ocr.calls == ["det"], "fallback must not run when detection succeeded"


@pytest.mark.parametrize("empty", [None, [], [None], [[]]])
def test_falls_back_to_recognition_when_detector_finds_nothing(empty):
    """The case that matters: no text box on a small crop."""
    ocr = FakeOCR(det_result=empty, rec_result=REC_HIT)
    assert _text_pieces(ocr, IMAGE) == [("GJ01AB1234", 0.71)]
    assert ocr.calls == ["det", "rec"]


def test_falls_back_when_detection_raises():
    ocr = FakeOCR(det_raises=True, rec_result=REC_HIT)
    assert _text_pieces(ocr, IMAGE) == [("GJ01AB1234", 0.71)]
    assert ocr.calls == ["det", "rec"]


def test_blank_recognitions_are_dropped():
    ocr = FakeOCR(det_result=None, rec_result=[[("", 0.0), ("GJ01AB1234", 0.6)]])
    assert _text_pieces(ocr, IMAGE) == [("GJ01AB1234", 0.6)]


@pytest.mark.parametrize("rec", [None, [], [[]]])
def test_no_text_anywhere_yields_nothing(rec):
    assert _text_pieces(FakeOCR(det_result=None, rec_result=rec), IMAGE) == []


def test_recognition_failure_is_not_fatal():
    """OCR runs inside the ingest loop; a crash here must not kill the camera."""
    assert _text_pieces(FakeOCR(det_result=None, rec_raises=True), IMAGE) == []
