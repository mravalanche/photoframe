"""Authoritative automatic schedule calculations in the device timezone."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import FrameSettings, ScheduleMode

CATCH_UP_WINDOW = timedelta(hours=2)


@dataclass(frozen=True)
class ScheduleOccurrence:
    due_at: datetime
    key: str


def _wall_time(value: str) -> time:
    return time.fromisoformat(value)


def _resolve_wall_time(day: date, value: str, zone: ZoneInfo) -> datetime:
    """Resolve a local wall time, choosing the first fold and advancing gaps."""
    naive = datetime.combine(day, _wall_time(value))
    for minute in range(181):
        candidate = naive + timedelta(minutes=minute)
        for fold in (0, 1):
            aware = candidate.replace(tzinfo=zone, fold=fold)
            round_trip = aware.astimezone(UTC).astimezone(zone)
            if round_trip.replace(tzinfo=None) == candidate and round_trip.fold == fold:
                return aware.astimezone(UTC)
    raise ValueError("No valid local schedule time was found within three hours")


def _calendar_day(frame: FrameSettings, local_day: date, direction: int) -> date:
    if frame.schedule_mode == ScheduleMode.DAILY:
        return local_day
    distance = (frame.weekly_day - local_day.weekday()) % 7
    if direction < 0:
        distance = -((local_day.weekday() - frame.weekly_day) % 7)
    return local_day + timedelta(days=distance)


def _calendar_time(frame: FrameSettings) -> str:
    return frame.daily_time if frame.schedule_mode == ScheduleMode.DAILY else frame.weekly_time


def next_occurrence(frame: FrameSettings, timezone: str, after: datetime) -> ScheduleOccurrence:
    """Return the first automatic occurrence strictly after ``after``."""
    current = after.astimezone(UTC)
    if frame.schedule_mode == ScheduleMode.INTERVAL:
        anchor = frame.schedule_anchor.astimezone(UTC)
        elapsed = max(0, (current - anchor).total_seconds())
        slot = int(elapsed // frame.rotation_seconds) + 1
        due = anchor + timedelta(seconds=slot * frame.rotation_seconds)
        return ScheduleOccurrence(due, f"interval:{anchor.isoformat()}:{slot}")

    zone = ZoneInfo(timezone)
    local_day = _calendar_day(frame, current.astimezone(zone).date(), 1)
    due = _resolve_wall_time(local_day, _calendar_time(frame), zone)
    step = 1 if frame.schedule_mode == ScheduleMode.DAILY else 7
    while due <= current:
        local_day += timedelta(days=step)
        due = _resolve_wall_time(local_day, _calendar_time(frame), zone)
    return ScheduleOccurrence(due, f"{frame.schedule_mode.value}:{local_day.isoformat()}")


def latest_occurrence(
    frame: FrameSettings, timezone: str, at: datetime
) -> ScheduleOccurrence | None:
    """Return the most recent due occurrence, excluding an interval's anchor."""
    current = at.astimezone(UTC)
    if frame.schedule_mode == ScheduleMode.INTERVAL:
        anchor = frame.schedule_anchor.astimezone(UTC)
        elapsed = (current - anchor).total_seconds()
        if elapsed < frame.rotation_seconds:
            return None
        slot = int(elapsed // frame.rotation_seconds)
        due = anchor + timedelta(seconds=slot * frame.rotation_seconds)
        return ScheduleOccurrence(due, f"interval:{anchor.isoformat()}:{slot}")

    zone = ZoneInfo(timezone)
    local_day = _calendar_day(frame, current.astimezone(zone).date(), -1)
    due = _resolve_wall_time(local_day, _calendar_time(frame), zone)
    step = 1 if frame.schedule_mode == ScheduleMode.DAILY else 7
    if due > current:
        local_day -= timedelta(days=step)
        due = _resolve_wall_time(local_day, _calendar_time(frame), zone)
    return ScheduleOccurrence(due, f"{frame.schedule_mode.value}:{local_day.isoformat()}")


def catch_up_occurrence(
    frame: FrameSettings, timezone: str, now: datetime
) -> ScheduleOccurrence | None:
    latest = latest_occurrence(frame, timezone, now)
    if latest is None or now.astimezone(UTC) - latest.due_at > CATCH_UP_WINDOW:
        return None
    return latest
