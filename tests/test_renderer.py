from datetime import UTC, datetime, timedelta
from threading import Event

from photoframe.models import DeviceSettings
from photoframe.renderer import MockRenderCoordinator, RenderPhase
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


def test_schedule_label_uses_london_24_hour_time():
    now = datetime(2026, 8, 23, 18, 48, tzinfo=UTC)
    target = datetime(2026, 8, 23, 19, 30, tzinfo=UTC)
    assert schedule_label(target, "Europe/London", now) == "20:30 · in 42 min"
