from datetime import UTC, datetime

import pytest

from photoframe.models import DisplayDriver
from photoframe.providers import DemoProvider
from photoframe.services.configuration import ConfigurationService
from photoframe.services.runtime import Runtime
from photoframe.settings import SecretStore, SettingsRepository
from photoframe.web.forms import HardwareForm, WorkflowForm


def hardware_fields(**overrides):
    return {
        "display_driver": "mock",
        "display_model": "Test panel",
        "display_width_px": "1200",
        "display_height_px": "800",
        "expected_refresh_seconds": "20",
        "render_timeout_seconds": "60",
        **overrides,
    }


def schedule_fields():
    return {
        "orientation": "landscape",
        "rotation_seconds": "3600",
        "schedule_mode": "daily",
        "daily_time": "06:30",
    }


def configured_service(tmp_path):
    repository = SettingsRepository(tmp_path)
    secrets = SecretStore(tmp_path)
    runtime = Runtime(repository, secrets, lambda _kind: DemoProvider())

    def configure(settings):
        settings.device.display_driver = DisplayDriver.MOCK
        settings.device.display_model = "Existing panel"
        settings.device.set_display_size(640, 480)
        settings.frame.schedule_anchor = datetime(2026, 1, 1, tzinfo=UTC)
        settings.refresh_status.last_attempted_schedule_key = "test-slot"
        settings.refresh_status.last_completed_schedule_key = "test-slot"
        settings.refresh_status.last_rendered_photo_id = "existing"

    repository.update(configure)
    runtime.set_preview("pending")
    return ConfigurationService(repository, secrets, runtime, tmp_path), runtime


def test_hardware_save_preserves_schedule_selection_history_and_mock_driver(tmp_path):
    service, runtime = configured_service(tmp_path)
    before = runtime.repository.load()

    notice = service.save_hardware(HardwareForm.parse(hardware_fields()))

    after = runtime.repository.load()
    assert "schedule is unchanged" in notice
    assert after.frame == before.frame
    assert after.refresh_status == before.refresh_status
    assert after.device.timezone == before.device.timezone
    assert after.device.display_driver == DisplayDriver.MOCK
    assert after.device.display_size == (1200, 800)
    assert after.device.display_model == "Test panel"
    assert after.device.expected_refresh_seconds == 20
    assert after.device.render_timeout_seconds == 60
    assert runtime.preview_id() == "pending"
    assert runtime.prepared_image is None
    assert not runtime.renderer.snapshot().active


def test_schedule_only_save_preserves_hardware_without_probing_display(tmp_path, monkeypatch):
    service, runtime = configured_service(tmp_path)
    before = runtime.repository.load()
    monkeypatch.setattr(
        runtime, "initialise_display", lambda: pytest.fail("Schedule must not probe hardware")
    )

    service.save_workflow(WorkflowForm.parse(schedule_fields()))

    after = runtime.repository.load()
    assert after.device == before.device
    assert after.frame.daily_time == "06:30"
    assert after.frame.schedule_anchor != before.frame.schedule_anchor


def test_legacy_workflow_submission_still_saves_hardware(tmp_path):
    service, runtime = configured_service(tmp_path)

    service.save_workflow(WorkflowForm.parse({**schedule_fields(), **hardware_fields()}))

    after = runtime.repository.load()
    assert after.frame.daily_time == "06:30"
    assert after.device.display_driver == DisplayDriver.MOCK
    assert after.device.display_size == (1200, 800)
    assert after.device.expected_refresh_seconds == 20


@pytest.mark.parametrize(
    "fields",
    [
        hardware_fields(display_height_px=""),
        hardware_fields(display_width_px="0"),
        hardware_fields(render_timeout_seconds="9"),
        hardware_fields(expected_refresh_seconds="4"),
    ],
)
def test_invalid_hardware_never_partially_updates_settings(tmp_path, fields):
    service, runtime = configured_service(tmp_path)
    before = runtime.repository.load()

    with pytest.raises(ValueError):
        service.save_hardware(HardwareForm.parse(fields))

    assert runtime.repository.load() == before
    with runtime.interactive_operation():
        pass


def test_hardware_save_refuses_active_render_without_changes(tmp_path):
    service, runtime = configured_service(tmp_path)
    runtime.renderer.start("existing")
    before = runtime.repository.load()

    with pytest.raises(ValueError, match="Wait for the current frame update"):
        service.save_hardware(HardwareForm.parse(hardware_fields()))

    assert runtime.repository.load() == before


def test_partial_legacy_hardware_submission_is_rejected():
    with pytest.raises(ValueError):
        WorkflowForm.parse({**schedule_fields(), "display_width_px": "1200"})


def test_hardware_initialization_excludes_scheduled_render_and_other_mutations(
    tmp_path, monkeypatch
):
    service, runtime = configured_service(tmp_path)

    def initialize():
        assert not runtime.refresh_lifecycle()
        with pytest.raises(ValueError, match="album change"), runtime.interactive_operation():
            pytest.fail("Mutation admitted during hardware initialization")
        assert not runtime.maintenance_gate.enter_and_drain(0)

    monkeypatch.setattr(runtime, "initialise_display", initialize)

    service.save_hardware(HardwareForm.parse(hardware_fields()))

    with runtime.interactive_operation():
        pass
