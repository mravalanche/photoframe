from datetime import UTC, datetime, timedelta

from photoframe.models import FrameSettings, Orientation, Photo, PhotoOrder
from photoframe.selector import active_selection, classify_photos, shuffled_photo_ids


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


def test_eligibility_reports_orientation_and_decode_failures_separately():
    photos = [
        Photo(id="ready", filename="ready.jpg", width=1200, height=800),
        Photo(id="portrait", filename="portrait.jpg", width=800, height=1200),
        Photo(id="heic", filename="unsupported.heic", width=1200, height=800),
    ]

    summary = classify_photos(photos, FrameSettings(), lambda photo: photo.id != "heic")

    assert [photo.id for photo in summary.eligible] == ["ready"]
    assert summary.wrong_orientation == 1
    assert summary.unsupported == 1


def test_shuffle_deck_is_stable_complete_and_avoids_boundary_repeat():
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    photos = [
        Photo(id=photo_id, filename=f"{photo_id}.jpg", width=1200, height=800)
        for photo_id in ("a", "b", "c", "d")
    ]
    frame = FrameSettings(
        photo_order=PhotoOrder.SHUFFLE,
        shuffle_seed=42,
        rotation_seconds=60,
        schedule_anchor=anchor,
    )
    frame.shuffle_photo_ids = shuffled_photo_ids(photos, frame)
    selected = [
        active_selection(photos, frame, anchor + timedelta(seconds=slot * 60)).photo.id
        for slot in range(5)
    ]
    assert len(set(selected[:4])) == 4
    assert selected[0] == selected[4]
    assert selected[3] != selected[4]


def test_shuffle_reconciles_catalog_changes_and_can_anchor_a_fresh_round():
    photos = [
        Photo(id=photo_id, filename=f"{photo_id}.jpg", width=1200, height=800)
        for photo_id in ("a", "b", "c")
    ]
    frame = FrameSettings(
        photo_order=PhotoOrder.SHUFFLE,
        shuffle_seed=7,
        shuffle_photo_ids=["removed", "b", "a"],
    )
    assert shuffled_photo_ids(photos, frame, anchor_id="c") == ["c", "b", "a"]
