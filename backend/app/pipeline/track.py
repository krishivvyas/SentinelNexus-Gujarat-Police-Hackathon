"""Lightweight single-camera vehicle tracker.

Associates detections across frames by IoU so that many observations of the same
vehicle become one sighting. That is what makes multi-frame plate voting possible
and stops the database filling with a row per frame.

Deliberately simple: no Kalman filter, no re-identification network. On a
CPU-only host the budget belongs to detection and OCR, and greedy IoU matching is
sufficient at the sampling rates we run.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .detect import Detection
from .ocr import PlateVoter


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    """One vehicle followed across frames on a single camera."""

    track_id: int
    detection: Detection
    first_pts_ms: float
    last_pts_ms: float
    first_event_ts: object = None          # datetime from the overlay clock
    last_event_ts: object = None
    hits: int = 1
    misses: int = 0
    voter: PlateVoter = field(default_factory=PlateVoter)
    best_frame: object = None              # numpy array, highest-confidence view
    best_confidence: float = 0.0
    label_votes: dict[str, int] = field(default_factory=dict)
    colour_votes: dict[str, int] = field(default_factory=dict)
    start_centre: tuple[float, float] | None = None
    reported: bool = False

    def update(self, detection: Detection, pts_ms: float, event_ts=None) -> None:
        self.detection = detection
        self.last_pts_ms = pts_ms
        if event_ts is not None:
            self.last_event_ts = event_ts
            if self.first_event_ts is None:
                self.first_event_ts = event_ts
        self.hits += 1
        self.misses = 0
        self.label_votes[detection.label] = self.label_votes.get(detection.label, 0) + 1
        if detection.colour:
            self.colour_votes[detection.colour] = self.colour_votes.get(detection.colour, 0) + 1

    @property
    def centre(self) -> tuple[float, float]:
        d = self.detection
        return d.x + d.w / 2, d.y + d.h / 2

    @property
    def label(self) -> str:
        return max(self.label_votes, key=self.label_votes.get) if self.label_votes \
            else self.detection.label

    @property
    def colour(self) -> str:
        return max(self.colour_votes, key=self.colour_votes.get) if self.colour_votes else ""

    @property
    def direction(self) -> str:
        """Compass-ish direction of travel across the frame."""
        if self.start_centre is None:
            return ""
        dx = self.centre[0] - self.start_centre[0]
        dy = self.centre[1] - self.start_centre[1]
        if abs(dx) < 12 and abs(dy) < 12:
            return "stationary"
        if abs(dx) >= abs(dy):
            return "eastbound" if dx > 0 else "westbound"
        return "southbound" if dy > 0 else "northbound"

    @property
    def duration_ms(self) -> float:
        return max(0.0, self.last_pts_ms - self.first_pts_ms)


class VehicleTracker:
    """Greedy IoU tracker with a miss tolerance."""

    def __init__(self, iou_threshold: float = 0.25, max_misses: int = 3,
                 min_hits_to_report: int = 2) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        # A vehicle seen in only one frame is usually a false positive.
        self.min_hits_to_report = min_hits_to_report
        self.tracks: list[Track] = []
        self._ids = itertools.count(1)

    def reset(self) -> list[Track]:
        """Drop all state and return whatever was still open.

        Called at a scene discontinuity (the feed loop point): the next frame is
        an unrelated moment, so carrying tracks across it would invent journeys.
        """
        finished = [t for t in self.tracks if t.hits >= self.min_hits_to_report]
        self.tracks = []
        return finished

    def update(self, detections: list[Detection], pts_ms: float,
               event_ts=None) -> tuple[list[Track], list[Track]]:
        """Advance the tracker one frame.

        Returns ``(active_tracks, finished_tracks)``.
        """
        unmatched = list(range(len(detections)))
        pairs: list[tuple[float, int, int]] = []

        for ti, track in enumerate(self.tracks):
            for di in unmatched:
                score = iou(track.detection.bbox, detections[di].bbox)
                if score >= self.iou_threshold:
                    pairs.append((score, ti, di))

        pairs.sort(reverse=True)
        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        for score, ti, di in pairs:
            if ti in used_tracks or di in used_dets:
                continue
            self.tracks[ti].update(detections[di], pts_ms, event_ts)
            used_tracks.add(ti)
            used_dets.add(di)

        # Unmatched existing tracks age out.
        finished: list[Track] = []
        surviving: list[Track] = []
        for ti, track in enumerate(self.tracks):
            if ti in used_tracks:
                surviving.append(track)
                continue
            track.misses += 1
            if track.misses > self.max_misses:
                if track.hits >= self.min_hits_to_report:
                    finished.append(track)
            else:
                surviving.append(track)
        self.tracks = surviving

        # Unmatched detections start new tracks.
        for di, detection in enumerate(detections):
            if di in used_dets:
                continue
            track = Track(
                track_id=next(self._ids),
                detection=detection,
                first_pts_ms=pts_ms,
                last_pts_ms=pts_ms,
                first_event_ts=event_ts,
                last_event_ts=event_ts,
            )
            track.start_centre = track.centre
            track.label_votes[detection.label] = 1
            if detection.colour:
                track.colour_votes[detection.colour] = 1
            self.tracks.append(track)

        return list(self.tracks), finished

    def flush(self) -> list[Track]:
        """Close every open track -- used at shutdown."""
        finished = [t for t in self.tracks if t.hits >= self.min_hits_to_report]
        self.tracks = []
        return finished
