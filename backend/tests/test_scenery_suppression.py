"""Fixed scene structure must not be recorded as passing vehicles.

Detection runs on COCO-trained weights, so a toll gantry, a booth cabin or a
barrier post lands on a vehicle class whenever it happens to look like one. On
CAM-12's toll forecourt the booth cabin reads as "bus" at 0.52 confidence. What
separates structure from traffic is the one thing structure never does: move.
"""
import pytest

from app.pipeline.detect import Detection
from app.pipeline.track import STATIC_MIN_HITS, Track


def make_track(x=100, y=100, w=120, h=80, label="car") -> Track:
    """A freshly started track, wired up the way VehicleTracker does it."""
    detection = Detection(x=x, y=y, w=w, h=h, label=label, confidence=0.6)
    track = Track(track_id=1, detection=detection, first_pts_ms=0.0, last_pts_ms=0.0)
    track.start_centre = track.centre
    track.label_votes[label] = 1
    return track


def advance(track: Track, frames: int, dx: float = 0.0, dy: float = 0.0) -> Track:
    """Re-detect the track `frames` times, shifting the box by (dx, dy) each time."""
    d = track.detection
    for i in range(1, frames + 1):
        moved = Detection(x=int(d.x + dx * i), y=int(d.y + dy * i),
                          w=d.w, h=d.h, label=d.label, confidence=d.confidence)
        track.update(moved, pts_ms=400.0 * i)
    return track


def test_immobile_long_lived_track_is_scenery():
    track = advance(make_track(), frames=STATIC_MIN_HITS + 5)
    assert track.max_drift_px == 0.0
    assert track.is_static_scenery


def test_moving_vehicle_is_never_scenery():
    track = advance(make_track(), frames=STATIC_MIN_HITS + 5, dx=6.0)
    assert track.max_drift_px > 8.0
    assert not track.is_static_scenery


def test_briefly_stopped_vehicle_is_not_scenery():
    """A vehicle held at a light is a real sighting -- it just has not moved yet."""
    track = advance(make_track(), frames=STATIC_MIN_HITS - 5)
    assert track.hits < STATIC_MIN_HITS
    assert not track.is_static_scenery


def test_vehicle_that_stops_then_departs_is_not_scenery():
    """Drift is measured against the furthest the box ever got, not first vs last.

    A vehicle that waits, moves off, and happens to be last seen near where it
    started would look immobile to a first-versus-last comparison.
    """
    track = make_track()
    advance(track, frames=STATIC_MIN_HITS)          # waiting, no movement
    d = track.detection
    for i in range(1, 11):                          # then drives away...
        track.update(Detection(x=d.x + 40 * i, y=d.y, w=d.w, h=d.h,
                               label=d.label, confidence=d.confidence),
                     pts_ms=100_000.0 + i)
    for i in range(1, 11):                          # ...and back again
        track.update(Detection(x=d.x + 40 * (10 - i), y=d.y, w=d.w, h=d.h,
                               label=d.label, confidence=d.confidence),
                     pts_ms=200_000.0 + i)
    assert track.centre == track.start_centre, "ends where it began"
    assert not track.is_static_scenery


def test_a_single_detection_is_not_scenery():
    """A brand-new track has no movement history and must not be suppressed."""
    assert not make_track().is_static_scenery


@pytest.mark.parametrize("jitter", [0.0, 0.1, 0.15])
def test_box_jitter_still_counts_as_scenery(jitter):
    """Detector output wobbles by a pixel or two; that is not movement."""
    track = advance(make_track(), frames=STATIC_MIN_HITS + 5, dx=jitter)
    assert track.is_static_scenery
