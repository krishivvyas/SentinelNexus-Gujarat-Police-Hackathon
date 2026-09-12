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
import math
from dataclasses import dataclass, field

from .detect import Detection
from .ocr import PlateVoter

#: A track that is detected this many times while never moving is scenery, not a
#: vehicle. Toll gantries, booth cabins, barrier posts and roadside furniture all
#: land on a COCO class often enough to be detected every frame -- on CAM-12 the
#: booth cabin reads as "bus" at 0.52 -- and each one otherwise becomes a
#: sighting. At the default 400 ms sampling this is ~16 s of continuous
#: detection.
STATIC_MIN_HITS = 40
#: ...and never straying further than this from where it was first seen. The test
#: is against the furthest the box ever got, not first-versus-last, so a vehicle
#: that stops and later drives away is never mistaken for scenery. What it does
#: suppress is a vehicle parked for the whole time it is visible -- which is not
#: passing traffic, and would otherwise re-report the same parked car all day.
STATIC_MAX_DRIFT_PX = 8.0


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
    # The plate region that produced this track's best accepted read, kept so a
    # sighting can show the operator *what was actually read* rather than a
    # picture of the whole car with the claim written underneath it. Held only
    # for an accepted read: a crop that OCR rejected is not evidence of a plate.
    best_plate_crop: object = None         # numpy array, the plate region
    best_plate_confidence: float = 0.0
    label_votes: dict[str, int] = field(default_factory=dict)
    start_centre: tuple[float, float] | None = None
    max_drift_px: float = 0.0              # furthest the centre ever got from start
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
        if self.start_centre is not None:
            cx, cy = self.centre
            drift = math.hypot(cx - self.start_centre[0], cy - self.start_centre[1])
            self.max_drift_px = max(self.max_drift_px, drift)

    @property
    def centre(self) -> tuple[float, float]:
        d = self.detection
        return d.x + d.w / 2, d.y + d.h / 2

    @property
    def label(self) -> str:
        return max(self.label_votes, key=self.label_votes.get) if self.label_votes \
            else self.detection.label

    @property
    def is_static_scenery(self) -> bool:
        """True when this track is a fixed part of the scene rather than a vehicle.

        Detection runs on COCO-trained weights, so roadside structure lands on a
        vehicle class whenever it happens to look like one. Structure is
        distinguished from traffic by the one thing it never does: move.
        """
        return self.hits >= STATIC_MIN_HITS and self.max_drift_px <= STATIC_MAX_DRIFT_PX

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
                 min_hits_to_report: int = 2,
                 motion_radius_factor: float = 2.5) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        # A vehicle seen in only one frame is usually a false positive.
        self.min_hits_to_report = min_hits_to_report
        # How far, in multiples of its own box size, a vehicle may travel between
        # samples and still be considered the same vehicle. See the displacement
        # pass in update() for why IoU alone is not enough here.
        self.motion_radius_factor = motion_radius_factor
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

        # Displacement pass. A small, fast vehicle moves clear of its own box
        # between samples, so every IoU is 0 and the greedy pass above never
        # matches it -- measured on this grid at 400 ms sampling, median IoU
        # against the previous frame is 0.84 for a bus and 0.00 for a
        # motorcycle. Without this, two-wheelers are detected every frame, never
        # accumulate hits, and never reach min_hits_to_report: they are seen and
        # then thrown away. Matching falls back to centre displacement, gated by
        # box size, label and area ratio so it cannot join unrelated vehicles.
        for ti, track in enumerate(self.tracks):
            if ti in used_tracks:
                continue
            tx, ty, tw, th = track.detection.bbox
            t_centre = (tx + tw / 2, ty + th / 2)
            t_area = max(tw * th, 1)
            best_di: int | None = None
            best_distance = self.motion_radius_factor * max(tw, th)

            for di, detection in enumerate(detections):
                if di in used_dets or detection.label != track.detection.label:
                    continue
                dx, dy, dw, dh = detection.bbox
                # A plausible match keeps roughly the same apparent size.
                if not 0.5 <= (dw * dh) / t_area <= 2.0:
                    continue
                distance = math.hypot(dx + dw / 2 - t_centre[0],
                                      dy + dh / 2 - t_centre[1])
                if distance < best_distance:
                    best_di, best_distance = di, distance

            if best_di is not None:
                track.update(detections[best_di], pts_ms, event_ts)
                used_tracks.add(ti)
                used_dets.add(best_di)

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
            self.tracks.append(track)

        return list(self.tracks), finished

    def flush(self) -> list[Track]:
        """Close every open track -- used at shutdown."""
        finished = [t for t in self.tracks if t.hits >= self.min_hits_to_report]
        self.tracks = []
        return finished
