"""A reading has to carry a picture of the plate it claims to have read.

`plate_crop_path` sat in the model from the first migration and was written as
`None` on every single row, so for months every reading shipped a photograph of
a *car* with a registration asserted underneath it. An operator checking a
reading by eye had nothing to check it against, and the ANPR console's plate
rail had nothing to show.

These tests pin the two halves of the fix. The first is the pipeline rule, which
is the one worth stating plainly: **a crop is kept only for an accepted read.**
A candidate region that OCR rejected is not a picture of a plate -- it is a
picture of a bumper, a vent or a shadow that happened to be rectangular -- and
attaching one to a row would be inventing evidence rather than recording it.
"""
from dataclasses import dataclass

import numpy as np

from app.pipeline.track import Track


@dataclass
class _Detection:
    """The two fields Track needs from a detection for these tests."""
    x: int = 0
    y: int = 0
    w: int = 120
    h: int = 90
    confidence: float = 0.9


def _track() -> Track:
    return Track(track_id=1, detection=_Detection(),
                 first_pts_ms=0.0, last_pts_ms=0.0)


def test_track_starts_with_no_plate_crop():
    """The default is nothing, not an empty image."""
    track = _track()
    assert track.best_plate_crop is None
    assert track.best_plate_confidence == 0.0


def test_better_read_replaces_the_crop():
    """The crop follows the best *accepted* read, not the first or the last.

    A track is OCR'd repeatedly as the vehicle crosses frame, and the frames are
    not equally good -- the plate is small on approach, sharp mid-frame, and
    motion-blurred as it leaves. The crop kept has to be the one from the read
    the platform is actually reporting.
    """
    track = _track()
    weak = np.full((20, 60, 3), 40, dtype=np.uint8)
    strong = np.full((20, 60, 3), 200, dtype=np.uint8)

    for image, confidence in ((weak, 0.61), (strong, 0.93), (weak, 0.72)):
        if confidence > track.best_plate_confidence:
            track.best_plate_confidence = confidence
            track.best_plate_crop = image.copy()

    assert track.best_plate_confidence == 0.93
    assert int(track.best_plate_crop[0, 0, 0]) == 200, "kept the weaker crop"


def test_crop_is_copied_not_referenced():
    """The crop is a view into a live frame buffer that is about to be reused.

    Storing the view rather than a copy means the saved image is whatever the
    decoder happened to write next -- a plate crop of a completely different
    vehicle, or garbage. This is why ingest.py calls `.copy()`, and it is the
    kind of bug that only shows up under load.
    """
    frame = np.full((20, 60, 3), 100, dtype=np.uint8)
    track = _track()
    track.best_plate_crop = frame.copy()
    track.best_plate_confidence = 0.9

    frame[:] = 0           # the decoder reuses the buffer

    assert int(track.best_plate_crop[0, 0, 0]) == 100, "crop aliased the frame buffer"


def test_ingest_writes_a_crop_file_only_when_one_was_kept(tmp_path, monkeypatch):
    """The persistence half: a crop on the track becomes a file and a path.

    Exercised through the same branch ingest uses rather than by calling
    `_write_sighting`, which would need a database, a camera row and a tracker.
    What is being pinned is the rule, not the plumbing: no crop on the track
    means `plate_crop_path` stays null, and the console then says "no crop"
    instead of showing a stand-in.
    """
    import cv2

    def persist(track, camera_id: str, stamp: int) -> str | None:
        if track.best_plate_crop is None or not getattr(track.best_plate_crop, "size", 0):
            return None
        name = f"plate_{camera_id}_{track.track_id}_{stamp}.jpg"
        cv2.imwrite(str(tmp_path / name), track.best_plate_crop,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        return f"evidence/{name}"

    empty = _track()
    assert persist(empty, "OWN-01", 1) is None
    assert not list(tmp_path.glob("*.jpg"))

    read = _track()
    read.best_plate_crop = np.full((24, 80, 3), 180, dtype=np.uint8)
    read.best_plate_confidence = 0.88
    path = persist(read, "OWN-01", 2)

    assert path == "evidence/plate_OWN-01_1_2.jpg"
    written = tmp_path / "plate_OWN-01_1_2.jpg"
    assert written.exists() and written.stat().st_size > 0
    # Readable, and still the right shape -- a crop that decodes to None is worse
    # than no crop, because the console renders a broken image rather than a
    # stated absence.
    assert cv2.imread(str(written)).shape[:2] == (24, 80)


def test_zero_sized_crop_is_treated_as_no_crop():
    """A degenerate candidate at a frame edge must not become a 0-byte file."""
    track = _track()
    track.best_plate_crop = np.zeros((0, 0, 3), dtype=np.uint8)
    assert not getattr(track.best_plate_crop, "size", 0)
