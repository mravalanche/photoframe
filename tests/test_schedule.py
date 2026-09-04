import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock, Thread

from fastapi.testclient import TestClient

from photoframe.models import AppSettings, FrameSettings, ScheduleMode
from photoframe.schedule import CATCH_UP_WINDOW, catch_up_occurrence, next_occurrence
from photoframe.services.configuration import ConfigurationService
from photoframe.settings import SettingsRepository
from photoframe.web.routes import create_app


def test_clean_install_defaults_to_daily_three_in_device_timezone(tmp_path: Path):
    settings = SettingsRepository(tmp_path).load()

    assert settings.frame.schedule_mode == ScheduleMode.DAILY
    assert settings.frame.daily_time == "03:00"
    assert next_occurrence(
        settings.frame,
        "Europe/London",
        datetime(2026, 1, 5, 17, tzinfo=UTC),
    ).due_at == datetime(2026, 1, 6, 3, tzinfo=UTC)


def test_legacy_interval_migration_is_explicit_idempotent_and_retains_anchor(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    repository.path.write_text(
        "[frame]\nrotation_seconds = 300\nschedule_anchor = 2026-01-02T04:05:06Z\n"
    )

    first = repository.load()
    first_payload = repository.path.read_text()
    second = repository.load()

    assert first.frame.schedule_mode == second.frame.schedule_mode == ScheduleMode.INTERVAL
    assert first.frame.rotation_seconds == second.frame.rotation_seconds == 300
    assert first.frame.schedule_anchor == datetime(2026, 1, 2, 4, 5, 6, tzinfo=UTC)
    assert repository.path.read_text() == first_payload
    assert 'schedule_mode = "interval"' in first_payload
    assert "schema_version = 2" in first_payload


def test_interval_daily_and_weekly_next_occurrences():
    frame = FrameSettings(
        schedule_mode=ScheduleMode.INTERVAL,
        schedule_anchor=datetime(2026, 1, 1, tzinfo=UTC),
        rotation_seconds=3600,
    )
    assert next_occurrence(
        frame, "UTC", datetime(2026, 1, 1, 2, 20, tzinfo=UTC)
    ).due_at == datetime(2026, 1, 1, 3, tzinfo=UTC)

    frame.schedule_mode = ScheduleMode.DAILY
    frame.daily_time = "03:00"
    assert next_occurrence(
        frame, "Europe/London", datetime(2026, 1, 1, 4, tzinfo=UTC)
    ).due_at == datetime(2026, 1, 2, 3, tzinfo=UTC)

    frame.schedule_mode = ScheduleMode.WEEKLY
    frame.weekly_day = 0
    frame.weekly_time = "03:00"
    assert next_occurrence(
        frame, "Europe/London", datetime(2026, 1, 6, tzinfo=UTC)
    ).due_at == datetime(2026, 1, 12, 3, tzinfo=UTC)


def test_dst_gap_runs_at_next_valid_time_and_fold_runs_once():
    frame = FrameSettings(schedule_mode=ScheduleMode.DAILY, daily_time="01:30")

    gap = next_occurrence(frame, "Europe/London", datetime(2026, 3, 28, 23, tzinfo=UTC))
    assert gap.due_at == datetime(2026, 3, 29, 1, tzinfo=UTC)

    fold = next_occurrence(frame, "Europe/London", datetime(2026, 10, 24, 23, tzinfo=UTC))
    assert fold.due_at == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    after_first = next_occurrence(frame, "Europe/London", fold.due_at)
    assert after_first.due_at == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)


def test_catch_up_is_bounded_to_named_two_hour_window():
    frame = FrameSettings(
        schedule_mode=ScheduleMode.DAILY,
        daily_time="03:00",
        schedule_anchor=datetime(2026, 1, 1, tzinfo=UTC),
    )
    due = datetime(2026, 1, 2, 3, tzinfo=UTC)

    assert catch_up_occurrence(frame, "UTC", due + CATCH_UP_WINDOW) is not None
    assert catch_up_occurrence(frame, "UTC", due + CATCH_UP_WINDOW + timedelta(seconds=1)) is None


def test_new_calendar_schedule_never_catches_up_before_its_save_boundary():
    saved_at = datetime(2026, 1, 2, 17, tzinfo=UTC)
    frame = FrameSettings(
        schedule_mode=ScheduleMode.DAILY,
        daily_time="16:00",
        schedule_anchor=saved_at,
    )

    assert catch_up_occurrence(frame, "UTC", saved_at + timedelta(minutes=30)) is None
    assert next_occurrence(frame, "UTC", saved_at).due_at == datetime(2026, 1, 3, 16, tzinfo=UTC)


def _request_id(html: str) -> str:
    match = re.search(r'name="request_id" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _schedule_snapshot(settings: AppSettings) -> dict[str, object]:
    return {
        "mode": settings.frame.schedule_mode,
        "interval": settings.frame.rotation_seconds,
        "anchor": settings.frame.schedule_anchor,
        "daily": settings.frame.daily_time,
        "weekday": settings.frame.weekly_day,
        "weekly": settings.frame.weekly_time,
        "completed": settings.refresh_status.last_completed_schedule_key,
        "attempted": settings.refresh_status.last_attempted_schedule_key,
    }


def test_next_route_is_distinct_idempotent_and_schedule_neutral(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    with TestClient(app) as client:
        initial = client.get("/partials/workspace")
        before = _schedule_snapshot(runtime.repository.load())
        current_id = re.search(r'<img src="/thumbnail/([^"/]+)" alt="Photo currently', initial.text)
        assert current_id
        client.post("/photo/preview", data={"photo_id": "forest"})

        request_id = _request_id(initial.text)
        started = client.post("/photo/next", data={"request_id": request_id})
        state = runtime.renderer.snapshot()
        assert started.status_code == 200
        assert "automatic schedule is unchanged" in started.text
        assert "pending preview was cleared" in started.text
        assert state.photo_id != current_id.group(1)
        assert runtime.preview_id() is None
        assert _schedule_snapshot(runtime.repository.load()) == before

        duplicate = client.post("/photo/next", data={"request_id": request_id})
        concurrent = client.post("/photo/next", data={"request_id": "new-concurrent-request"})
        assert "already handled" in duplicate.text
        assert "Wait for the current frame update" in concurrent.text

        assert state.started_at
        runtime.renderer.update(
            runtime.repository.load().device,
            state.started_at
            + timedelta(seconds=runtime.repository.load().device.expected_refresh_seconds),
        )
        first_id = runtime.repository.load().refresh_status.last_rendered_photo_id
        assert first_id == state.photo_id
        assert _schedule_snapshot(runtime.repository.load()) == before

        client.post("/render/reset")
        second_page = client.get("/partials/workspace")
        second = client.post("/photo/next", data={"request_id": _request_id(second_page.text)})
        second_state = runtime.renderer.snapshot()
        assert second_state.photo_id != first_id
        assert "automatic schedule is unchanged" in second.text
        assert _schedule_snapshot(runtime.repository.load()) == before


def test_next_failure_preserves_displayed_photo_and_schedule(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    runtime.repository.update(
        lambda settings: (
            setattr(settings.refresh_status, "last_rendered_photo_id", "coast"),
            setattr(settings.device, "render_timeout_seconds", 10),
        )
    )
    with TestClient(app) as client:
        page = client.get("/partials/workspace")
        before = _schedule_snapshot(runtime.repository.load())
        client.post("/photo/next", data={"request_id": _request_id(page.text)})
        state = runtime.renderer.snapshot()
        assert state.started_at
        runtime.renderer.update(
            runtime.repository.load().device, state.started_at + timedelta(seconds=10)
        )
        failed = client.get("/partials/workspace")

    assert runtime.repository.load().refresh_status.last_rendered_photo_id == "coast"
    assert _schedule_snapshot(runtime.repository.load()) == before
    assert "Frame update failed" in failed.text
    assert "Try next photo" in failed.text
    assert "Retry Next photo" not in failed.text


def test_next_prefers_live_display_over_stale_persisted_cursor(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    runtime.photos = [photo for photo in runtime.photos if photo.id in {"coast", "forest"}]
    runtime.repository.update(
        lambda settings: setattr(settings.refresh_status, "last_rendered_photo_id", "coast")
    )
    runtime.renderer.last_rendered_photo_id = "forest"

    with TestClient(app) as client:
        page = client.get("/partials/workspace")
        before = _schedule_snapshot(runtime.repository.load())
        client.post("/photo/next", data={"request_id": _request_id(page.text)})

    assert runtime.renderer.snapshot().photo_id == "coast"
    assert _schedule_snapshot(runtime.repository.load()) == before


def test_manual_show_now_persists_the_authoritative_display_cursor(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    with TestClient(app) as client:
        client.post("/photo/preview", data={"photo_id": "forest"})
        client.post("/render/start")
        state = runtime.renderer.snapshot()
        assert state.started_at
        runtime.renderer.update(
            runtime.repository.load().device,
            state.started_at
            + timedelta(seconds=runtime.repository.load().device.expected_refresh_seconds),
        )

    assert runtime.repository.load().refresh_status.last_rendered_photo_id == "forest"


def test_simultaneous_next_requests_start_exactly_one_render(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    service = ConfigurationService(runtime.repository, runtime.secrets, runtime, tmp_path)
    before = _schedule_snapshot(runtime.repository.load())
    barrier = Barrier(3)
    results: list[str] = []
    results_lock = Lock()
    start_count = 0
    start_count_lock = Lock()
    original_start = runtime.renderer.start

    def counted_start(*args, **kwargs):
        nonlocal start_count
        with start_count_lock:
            start_count += 1
        return original_start(*args, **kwargs)

    runtime.renderer.start = counted_start  # type: ignore[method-assign]

    def request_next(request_id: str) -> None:
        barrier.wait()
        try:
            result = service.start_next_photo(request_id)
        except ValueError as exc:
            result = str(exc)
        with results_lock:
            results.append(result)

    threads = [
        Thread(target=request_next, args=("simultaneous-one",)),
        Thread(target=request_next, args=("simultaneous-two",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert start_count == 1
    assert sum("automatic schedule is unchanged" in result for result in results) == 1
    assert len(results) == 2
    assert _schedule_snapshot(runtime.repository.load()) == before


def test_next_disabled_reasons_are_exposed(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    runtime.photos = runtime.photos[:1]
    with TestClient(app) as client:
        one_photo = client.get("/partials/workspace")
    assert "Add another eligible photo to use Next photo" in one_photo.text
    assert 'aria-describedby="next-photo-description next-photo-disabled-reason"' in one_photo.text

    empty_app = create_app(tmp_path / "empty", demo_mode=False)
    with TestClient(empty_app) as client:
        no_album = client.get("/partials/workspace")
    assert "Choose an album first" in no_album.text


def test_weekly_schedule_save_preview_and_reload_round_trip(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    data = {
        "orientation": "landscape",
        "schedule_mode": "weekly",
        "rotation_seconds": "3600",
        "daily_time": "04:15",
        "weekly_day": "4",
        "weekly_time": "02:45",
        "photo_order": "album",
        "display_driver": "mock",
        "display_width_px": "1200",
        "display_height_px": "750",
        "expected_refresh_seconds": "8",
        "render_timeout_seconds": "90",
    }
    with TestClient(app) as client:
        preview = client.post("/schedule/preview", data=data)
        saved = client.post("/workflow", data=data)
        reloaded = client.get("/partials/workspace")

    settings = app.state.runtime.repository.load()
    assert preview.status_code == 200
    assert "· in " in preview.text
    assert settings.frame.schedule_mode == ScheduleMode.WEEKLY
    assert settings.frame.daily_time == "04:15"  # pending mode value is retained
    assert settings.frame.weekly_day == 4
    assert settings.frame.weekly_time == "02:45"
    for html in (saved.text, reloaded.text):
        assert 'value="weekly" selected' in html
        assert 'value="4" selected' in html
        assert 'name="weekly_time" type="time" value="02:45"' in html
        assert "Friday at 02:45" in html
        assert "Next update if saved:" in html
