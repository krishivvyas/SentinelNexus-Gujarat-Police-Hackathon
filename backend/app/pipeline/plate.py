"""Licence-plate localisation and recovery.

Localisation runs two ways and merges the results:

  * A single-class YOLO plate detector (``plate_detect.py``), when its weights
    are present. Far more precise than morphology at night, where a lit grille,
    a rear light cluster and a bumper reflector all look plate-shaped.
  * Classical morphology inside the vehicle box -- blackhat, Sobel, close,
    contour filter. Costs nothing, needs no weights, and still occasionally
    finds a plate at an angle the model was not trained on.

The recovery chain that follows matters more than either localiser on this grid:
plates run 20-40 px wide at night with motion blur and headlight bloom, so the
hard question is "are there enough pixels on the glyphs", not "where is it".
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import plate_detect

# Indian plates are wide rectangles. Single-row plates sit near 4.5:1; the
# two-row plates common on motorcycles and commercial vehicles are nearer 2:1.
MIN_ASPECT = 1.6
MAX_ASPECT = 6.5
MIN_PLATE_WIDTH_PX = 24


@dataclass(slots=True)
class PlateCandidate:
    x: int
    y: int
    w: int
    h: int
    score: float
    image: np.ndarray | None = None

    @property
    def aspect(self) -> float:
        return self.w / max(self.h, 1)


def _candidate_score(w: int, h: int, region: np.ndarray) -> float:
    """Rank candidates by how plate-like the region looks."""
    aspect = w / max(h, 1)
    # Closeness to a typical single-row plate.
    aspect_score = 1.0 - min(abs(aspect - 4.0) / 4.0, 1.0)

    # Plates are high-contrast: dark glyphs on a bright ground (or the reverse).
    contrast = float(region.std()) / 128.0 if region.size else 0.0
    contrast_score = min(contrast, 1.0)

    # Bigger is better, up to a point -- more pixels on the glyphs.
    size_score = min(w / 200.0, 1.0)

    return 0.45 * aspect_score + 0.35 * contrast_score + 0.20 * size_score


def _learned_candidates(vehicle_crop: np.ndarray) -> list[PlateCandidate]:
    """Plate boxes from the learned detector, scored above every morphological one.

    The +1.0 offset is not a confidence claim -- it is a strict ordering. When
    the model says "the plate is here", that answer should be tried first, and
    the morphological candidates remain only as a fallback for the frames it
    misses. Both still go through the same restoration and OCR voting, so a bad
    learned box loses the vote on its own merits.
    """
    candidates: list[PlateCandidate] = []
    for x, y, w, h, confidence in plate_detect.find_plates(vehicle_crop):
        image = vehicle_crop[y:y + h, x:x + w]
        if image.size == 0:
            continue
        candidates.append(PlateCandidate(x=x, y=y, w=w, h=h,
                                         score=1.0 + confidence,
                                         image=image.copy()))
    return candidates


def _iou(a: PlateCandidate, b: PlateCandidate) -> float:
    """Overlap between two candidates, used to drop duplicates across localisers."""
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1 = min(a.x + a.w, b.x + b.w)
    y1 = min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    overlap = (x1 - x0) * (y1 - y0)
    return overlap / float(a.w * a.h + b.w * b.h - overlap)


def find_plate_candidates(vehicle_crop: np.ndarray, *, max_candidates: int = 3
                          ) -> list[PlateCandidate]:
    """Locate plate-shaped regions inside a vehicle crop.

    Learned candidates first, then morphological ones that do not simply repeat
    a box already found. Every candidate is OCR'd and the best read wins, so
    ordering is about spending the OCR budget well rather than about excluding
    anything.
    """
    if vehicle_crop is None or vehicle_crop.size == 0:
        return []
    h, w = vehicle_crop.shape[:2]
    if w < MIN_PLATE_WIDTH_PX or h < 12:
        return []

    learned = _learned_candidates(vehicle_crop)
    if len(learned) >= max_candidates:
        return learned[:max_candidates]

    gray = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2GRAY)

    # Blackhat lifts dark glyphs sitting on a bright plate background.
    rect = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, rect)

    # Horizontal gradient: glyph edges are predominantly vertical strokes.
    grad = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=3)
    grad = np.absolute(grad)
    lo, hi = float(grad.min()), float(grad.max())
    if hi - lo < 1e-6:
        return []
    grad = (255 * (grad - lo) / (hi - lo)).astype(np.uint8)

    # Close the glyphs into a single blob, then threshold.
    grad = cv2.GaussianBlur(grad, (5, 5), 0)
    closed = cv2.morphologyEx(grad, cv2.MORPH_CLOSE, rect)
    _, thresh = cv2.threshold(closed, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thresh = cv2.erode(thresh, None, iterations=1)
    thresh = cv2.dilate(thresh, None, iterations=2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[PlateCandidate] = []
    for contour in contours:
        cx, cy, cw, ch = cv2.boundingRect(contour)
        if cw < MIN_PLATE_WIDTH_PX or ch < 8:
            continue
        aspect = cw / max(ch, 1)
        if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
            continue
        # A plate never fills the whole vehicle box.
        if cw > w * 0.95 or ch > h * 0.6:
            continue

        region = gray[cy:cy + ch, cx:cx + cw]
        if region.size == 0:
            continue

        candidate = PlateCandidate(
            x=cx, y=cy, w=cw, h=ch,
            score=_candidate_score(cw, ch, region),
            image=vehicle_crop[cy:cy + ch, cx:cx + cw].copy(),
        )
        # Skip anything the learned detector already found. Re-reading the same
        # pixels cannot change the answer and costs a full OCR pass.
        if any(_iou(candidate, existing) > 0.5 for existing in learned):
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return (learned + candidates)[:max_candidates]


def restore(plate_img: np.ndarray, *, target_height: int = 96) -> np.ndarray:
    """Restore a small, blurred plate crop into something OCR can read.

    Upscale first, then equalise, then sharpen. Order matters: sharpening before
    upscaling amplifies sensor noise into fake glyph edges.
    """
    if plate_img is None or plate_img.size == 0:
        return plate_img

    img = plate_img
    h, w = img.shape[:2]

    # Scale up to a workable glyph height, capped so we do not synthesise detail.
    if h < target_height:
        scale = min(target_height / max(h, 1), 6.0)
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img

    # CLAHE rather than global equalisation: headlight bloom otherwise dominates.
    gray = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)

    # Bilateral filter smooths noise while keeping glyph edges.
    gray = cv2.bilateralFilter(gray, 7, 55, 55)

    # Unsharp mask.
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gray = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)

    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def deskew(plate_img: np.ndarray) -> np.ndarray:
    """Straighten a plate imaged at an angle, using its dominant text axis."""
    if plate_img is None or plate_img.size == 0:
        return plate_img
    gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY) if plate_img.ndim == 3 else plate_img
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    coords = cv2.findNonZero(255 - thresh)
    if coords is None or len(coords) < 20:
        return plate_img

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    # Only correct plausible camera tilt; a large angle means we found noise.
    if abs(angle) < 1.0 or abs(angle) > 20.0:
        return plate_img

    h, w = plate_img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(plate_img, matrix, (w, h),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def prepare_for_ocr(plate_img: np.ndarray) -> list[np.ndarray]:
    """Produce several restorations of one plate.

    OCR is run over all of them and the best-scoring read wins. Cheap insurance:
    which variant works depends on lighting that changes camera to camera.
    """
    if plate_img is None or plate_img.size == 0:
        return []

    variants: list[np.ndarray] = []
    restored = restore(plate_img)
    variants.append(restored)

    straightened = deskew(restored)
    if straightened is not restored:
        variants.append(straightened)

    # A hard binarisation sometimes rescues a washed-out plate.
    gray = cv2.cvtColor(restored, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 25, 9)
    variants.append(cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR))

    return variants
