from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

from photoframe.models import DisplayDriver
from photoframe.providers import DemoProvider
from photoframe.settings import SecretStore, SettingsRepository
from photoframe.web import Runtime


class ImmediateDisplay:
    def show(self, _image):
        return None


class BlockingDisplay:
    def __init__(self):
        self.release = Event()

    def show(self, _image):
        self.release.wait(1)


class RecoveringProvider(DemoProvider):
    def __init__(self):
        super().__init__()
        self.offline = True
        self.photo_calls = 0

    def list_photos(self, album_id):
        self.photo_calls += 1
        if self.offline:
            from photoframe.providers import ProviderError

            raise ProviderError("offline at boot")
        return super().list_photos(album_id)


def wait_for_display(runtime: Runtime) -> None:
    for _ in range(100):
        if not runtime.renderer.hardware_busy:
            return
        Event().wait(0.001)
    raise AssertionError("display worker did not finish")


def configured_repository(tmp_path: Path, anchor: datetime) -> SettingsRepository:
    repository = SettingsRepository(tmp_path)
    settings = repository.load()
    settings.frame.album_id = "demo-album"
    settings.frame.schedule_anchor = anchor
    settings.frame.rotation_seconds = 30
    settings.device.display_driver = DisplayDriver.MOCK
    settings.device.expected_refresh_seconds = 5
    settings.device.set_display_size(1200, 750)
    settings.refresh_status.next_attempt_at = anchor + timedelta(days=1)
    repository.save(settings)
    return repository


def new_runtime(repository: SettingsRepository) -> Runtime:
    runtime = Runtime(
        repository,
        SecretStore(repository.data_dir),
        lambda _kind: DemoProvider(),
        DemoProvider(),
    )
    runtime.display = ImmediateDisplay()  # type: ignore[assignment]
    return runtime


def test_restart_restores_catalog_renders_due_slot_and_suppresses_completed(tmp_path: Path):
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    repository = configured_repository(tmp_path, anchor)

    runtime = new_runtime(repository)
    assert runtime.catalog_snapshot()[1] == []
    runtime.refresh_lifecycle(anchor)
    wait_for_display(runtime)
    saved = repository.load()
    assert runtime.catalog_snapshot()[1]
    assert saved.refresh_status.last_completed_schedule_slot == 0
    assert saved.refresh_status.last_rendered_photo_id

    restarted = new_runtime(repository)
    assert restarted.catalog_snapshot()[1] == []
    restarted.refresh_lifecycle(anchor + timedelta(seconds=7))
    assert restarted.catalog_snapshot()[1]
    assert restarted.renderer.snapshot().photo_id is None

    repository.update(
        lambda settings: setattr(settings.frame, "schedule_anchor", anchor + timedelta(seconds=10))
    )
    due_after_restart = new_runtime(repository)
    due_after_restart.refresh_lifecycle(anchor + timedelta(seconds=10))
    assert due_after_restart.renderer.snapshot().photo_id is not None


def test_schedule_timeout_is_persisted_and_late_success_recovers_health(tmp_path: Path):
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    repository = configured_repository(tmp_path, anchor)
    runtime = new_runtime(repository)
    display = BlockingDisplay()
    runtime.display = display  # type: ignore[assignment]
    runtime.refresh_lifecycle(anchor)
    started = runtime.renderer.snapshot().started_at
    assert started is not None
    runtime._advance_scheduled_render(started + timedelta(seconds=91))
    assert "timeout" in (repository.load().refresh_status.last_render_error or "")
    assert runtime.renderer.hardware_busy
    display.release.set()
    wait_for_display(runtime)
    for _ in range(100):
        if repository.load().refresh_status.last_render_error is None:
            break
        Event().wait(0.001)
    assert repository.load().refresh_status.last_render_error is None


def test_offline_restart_records_retry_then_recovers_and_renders(tmp_path: Path):
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    repository = configured_repository(tmp_path, anchor)
    provider = RecoveringProvider()
    runtime = Runtime(repository, SecretStore(tmp_path), lambda _kind: provider, provider)
    runtime.display = ImmediateDisplay()  # type: ignore[assignment]

    assert runtime.refresh_lifecycle(anchor)
    failed = repository.load().refresh_status
    assert failed.last_error == "offline at boot"
    assert failed.next_attempt_at == anchor + timedelta(seconds=300)
    assert provider.photo_calls == 1

    runtime.refresh_lifecycle(anchor + timedelta(seconds=15))
    waiting = repository.load().refresh_status
    assert provider.photo_calls == 1
    assert waiting.next_attempt_at == anchor + timedelta(seconds=300)

    provider.offline = False
    runtime.refresh_lifecycle(anchor + timedelta(seconds=300))
    wait_for_display(runtime)
    recovered = repository.load().refresh_status
    assert recovered.last_error is None
    assert recovered.last_completed_schedule_slot == 10


def test_concurrent_lifecycle_calls_coalesce_provider_work(tmp_path: Path):
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    repository = configured_repository(tmp_path, anchor)
    entered = Event()
    release = Event()

    class BlockingProvider(DemoProvider):
        calls = 0

        def list_photos(self, album_id):
            self.calls += 1
            entered.set()
            release.wait(1)
            return super().list_photos(album_id)

    provider = BlockingProvider()
    runtime = Runtime(repository, SecretStore(tmp_path), lambda _kind: provider, provider)
    first = Thread(target=lambda: runtime.refresh_lifecycle(anchor))
    first.start()
    assert entered.wait(1)
    assert runtime.refresh_lifecycle(anchor) is False
    release.set()
    first.join()
    assert provider.calls == 1


def test_reboot_during_failure_backoff_does_not_bypass_retry(tmp_path: Path):
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    repository = configured_repository(tmp_path, anchor)
    provider = RecoveringProvider()
    first = Runtime(repository, SecretStore(tmp_path), lambda _kind: provider, provider)
    first.display = ImmediateDisplay()  # type: ignore[assignment]
    first.refresh_lifecycle(anchor)
    deadline = repository.load().refresh_status.next_attempt_at
    assert provider.photo_calls == 1

    rebooted = Runtime(repository, SecretStore(tmp_path), lambda _kind: provider, provider)
    rebooted.display = ImmediateDisplay()  # type: ignore[assignment]
    provider.offline = False
    rebooted.refresh_lifecycle(anchor + timedelta(seconds=15))
    assert provider.photo_calls == 1
    assert repository.load().refresh_status.next_attempt_at == deadline

    rebooted.refresh_lifecycle(anchor + timedelta(seconds=300))
    wait_for_display(rebooted)
    recovered = repository.load().refresh_status
    assert recovered.last_error is None
    assert recovered.last_completed_schedule_slot == 10
