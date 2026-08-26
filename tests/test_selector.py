from datetime import UTC, datetime, timedelta

from photoframe.models import FrameSettings, Orientation, Photo
from photoframe.selector import active_selection


def test_selection_is_deterministic_and_rotates_from_chosen_start():
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    photos = [
        Photo(id="a", filename="a.jpg", width=1200, height=800),
        Photo(id="b", filename="b.jpg", width=1200, height=800),
        Photo(id="portrait", filename="p.jpg", width=800, height=1200),
    ]
    frame = FrameSettings(
        orientation=Orientation.LANDSCAPE,
        rotation_seconds=60,
        schedule_anchor=anchor,
        starting_photo_id="b",
    )
    first = active_selection(photos, frame, anchor + timedelta(seconds=59))
    second = active_selection(photos, frame, anchor + timedelta(seconds=60))
    assert (first.photo.id, first.eligible_count, first.position) == ("b", 2, 1)
    assert (second.photo.id, second.position) == ("a", 2)


def test_square_and_unknown_dimensions_are_not_eligible():
    photos = [
        Photo(id="square", filename="s", width=10, height=10),
        Photo(id="unknown", filename="u"),
    ]
    assert active_selection(photos, FrameSettings()).photo is None
