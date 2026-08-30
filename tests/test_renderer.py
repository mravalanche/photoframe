from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Thread

from photoframe.models import DeviceSettings
from photoframe.renderer import MockRenderCoordinator, RenderPhase, RenderService
from photoframe.web import schedule_label


def test_render_uses_phases_and_busy_timeout_not_percentage():
    start = datetime(2026, 8, 23, 20, tzinfo=UTC)
    settings = DeviceSettings(expected_refresh_seconds=8, render_timeout_seconds=20)
    renderer = MockRenderCoordinator()
    assert renderer.start("photo", start).phase == RenderPhase.PREPARING
    assert renderer.update(settings, start + timedelta(seconds=1)).phase == RenderPhase.SENDING
    assert renderer.update(settings, start + timedelta(seconds=3)).phase == RenderPhase.WAITING
    state = renderer.update(settings, start + timedelta(seconds=8))
    assert state.phase == RenderPhase.COMPLETE
    assert renderer.last_rendered_photo_id == "photo"


def test_render_times_out_when_busy_signal_never_clears():
    start = datetime(2026, 8, 23, 20, tzinfo=UTC)
    settings = DeviceSettings(expected_refresh_seconds=50, render_timeout_seconds=10)
    renderer = MockRenderCoordinator()
    renderer.start("photo", start)
    state = renderer.update(settings, start + timedelta(seconds=10))
    assert state.phase == RenderPhase.FAILED
    assert state.message is not None
    assert "busy signal" in state.message


def test_hardware_render_uses_blocking_driver_completion():
    renderer = MockRenderCoordinator()
    completed = Event()

    def refresh():
        completed.set()

    state = renderer.start_hardware("photo", refresh)
    assert state.phase in {RenderPhase.SENDING, RenderPhase.WAITING, RenderPhase.COMPLETE}
    assert completed.wait(1)
    # The worker has a tiny scheduling window after signalling its callback.
    for _ in range(100):
        if renderer.state.phase == RenderPhase.COMPLETE:
            break
    assert renderer.state.phase == RenderPhase.COMPLETE
    assert renderer.last_rendered_photo_id == "photo"


def test_timeout_does_not_release_single_flight_lock():
    renderer = MockRenderCoordinator()
    release = Event()

    def wait_for_release() -> None:
        release.wait(2)

    renderer.start_hardware("first", wait_for_release)
    started = renderer.state.started_at
    assert started is not None
    renderer.update(DeviceSettings(render_timeout_seconds=10), started + timedelta(seconds=20))
    assert renderer.state.phase == RenderPhase.FAILED
    assert renderer.hardware_busy
    renderer.start_hardware("second", lambda: None)
    assert renderer.state.photo_id == "first"
    release.set()
    for _ in range(100):
        if not renderer.hardware_busy:
            break
        Event().wait(0.001)
    assert not renderer.hardware_busy


def test_simultaneous_manual_and_scheduled_start_only_one_refreshes():
    renderer = MockRenderCoordinator()
    release = Event()
    start = Barrier(3)
    prepared: list[str] = []

    def refresh(_prepared: object) -> None:
        release.wait(1)

    service = RenderService(renderer, lambda photo_id: prepared.append(photo_id), refresh)

    def submit(photo_id: str):
        start.wait()
        service.start(photo_id)

    threads = [Thread(target=submit, args=("manual",)), Thread(target=submit, args=("scheduled",))]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()
    assert len(prepared) == 1
    assert renderer.hardware_busy
    release.set()


def test_timeout_persists_failure_then_late_success_reconciles():
    renderer = MockRenderCoordinator()
    release = Event()
    failures: list[str] = []
    successes: list[str] = []

    def wait_for_release() -> None:
        release.wait(1)

    renderer.start_hardware("photo", wait_for_release, successes.append, failures.append)
    started = renderer.snapshot().started_at
    assert started is not None
    renderer.update(DeviceSettings(render_timeout_seconds=10), started + timedelta(seconds=11))
    assert failures and renderer.hardware_busy
    renderer.start_hardware("queued", lambda: None)
    assert renderer.snapshot().photo_id == "photo"
    release.set()
    for _ in range(100):
        if successes:
            break
        Event().wait(0.001)
    assert successes == ["photo"]


def test_timeout_then_late_driver_failure_reconciles_and_releases_lock():
    renderer = MockRenderCoordinator()
    release = Event()
    failures: list[str] = []

    def fail_after_release():
        release.wait(1)
        raise RuntimeError("panel disconnected")

    renderer.start_hardware("photo", fail_after_release, on_failure=failures.append)
    started = renderer.snapshot().started_at
    assert started is not None
    renderer.update(DeviceSettings(render_timeout_seconds=10), started + timedelta(seconds=11))
    assert "timeout" in failures[0]
    release.set()
    for _ in range(100):
        if not renderer.hardware_busy:
            break
        Event().wait(0.001)
    assert failures[-1] == "panel disconnected"
    assert renderer.snapshot().phase == RenderPhase.FAILED
    assert not renderer.hardware_busy


def test_render_handoff_uses_immutable_prepared_value():
    renderer = MockRenderCoordinator()
    release = Event()
    shown: list[object] = []
    prepared = {"first": object(), "second": object()}

    def show(value: object):
        shown.append(value)
        release.wait(1)

    service = RenderService(renderer, prepared.__getitem__, show)
    service.start("first")
    service.start("second")
    assert shown == [prepared["first"]]
    release.set()


def test_schedule_label_uses_london_24_hour_time():
    now = datetime(2026, 8, 23, 18, 48, tzinfo=UTC)
    target = datetime(2026, 8, 23, 19, 30, tzinfo=UTC)
    assert schedule_label(target, "Europe/London", now) == "20:30 · in 42 min"
