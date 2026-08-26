from datetime import UTC, datetime, timedelta

from .models import ActiveSelection, FrameSettings, Photo


def active_selection(
    photos: list[Photo], frame: FrameSettings, now: datetime | None = None
) -> ActiveSelection:
    eligible = [photo for photo in photos if photo.matches(frame.orientation)]
    earliest = datetime.min.replace(tzinfo=UTC)
    eligible.sort(key=lambda photo: ((photo.taken_at or earliest).isoformat(), photo.id))
    if not eligible:
        return ActiveSelection(photo=None, eligible_count=0)
    if frame.starting_photo_id:
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
