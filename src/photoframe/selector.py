import hashlib
from datetime import UTC, datetime, timedelta

from .models import ActiveSelection, FrameSettings, Photo, PhotoOrder


def eligible_photos(photos: list[Photo], frame: FrameSettings) -> list[Photo]:
    eligible = [photo for photo in photos if photo.matches(frame.orientation)]
    earliest = datetime.min.replace(tzinfo=UTC)
    eligible.sort(key=lambda photo: ((photo.taken_at or earliest).isoformat(), photo.id))
    return eligible


def shuffled_photo_ids(
    photos: list[Photo], frame: FrameSettings, *, anchor_id: str | None = None
) -> list[str]:
    """Build a stable deck, retaining eligible existing cards across catalog changes."""
    eligible_ids = [photo.id for photo in eligible_photos(photos, frame)]
    retained = [photo_id for photo_id in frame.shuffle_photo_ids if photo_id in eligible_ids]
    missing = [photo_id for photo_id in eligible_ids if photo_id not in retained]
    missing.sort(
        key=lambda photo_id: hashlib.sha256(f"{frame.shuffle_seed}:{photo_id}".encode()).digest()
    )
    deck = retained + missing
    if anchor_id in deck:
        deck.remove(anchor_id)
        deck.insert(0, anchor_id)
    return deck


def active_selection(
    photos: list[Photo], frame: FrameSettings, now: datetime | None = None
) -> ActiveSelection:
    eligible = eligible_photos(photos, frame)
    if not eligible:
        return ActiveSelection(photo=None, eligible_count=0)
    if frame.photo_order == PhotoOrder.SHUFFLE:
        by_id = {photo.id: photo for photo in eligible}
        deck = shuffled_photo_ids(photos, frame, anchor_id=frame.starting_photo_id)
        eligible = [by_id[photo_id] for photo_id in deck]
    elif frame.starting_photo_id:
        start = next(
            (i for i, photo in enumerate(eligible) if photo.id == frame.starting_photo_id), 0
        )
        eligible = eligible[start:] + eligible[:start]
    current = now or datetime.now(UTC)
    anchor = frame.schedule_anchor
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    elapsed = max(0, int((current - anchor).total_seconds()))
    slot = elapsed // frame.rotation_seconds
    index = slot % len(eligible)
    return ActiveSelection(
        photo=eligible[index],
        eligible_count=len(eligible),
        position=index + 1,
        next_change_at=anchor + timedelta(seconds=(slot + 1) * frame.rotation_seconds),
    )
