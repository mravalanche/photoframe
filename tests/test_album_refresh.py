from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from threading import Event

import pytest
from PIL import Image

from photoframe.models import Album, Photo, PhotoOrder
from photoframe.providers import ProviderError
from photoframe.services.configuration import ConfigurationService
from photoframe.services.runtime import Runtime
from photoframe.settings import SecretStore, SettingsRepository
from photoframe.updater.maintenance import MaintenanceError


class RecoveringProvider:
    def __init__(self):
        self.photos = [Photo(id="one", filename="one.jpg", width=20, height=10)]
        self.fail = False
        self.preview_available = False
        self.thumbnail_calls = 0
        output = BytesIO()
        with Image.new("RGB", (20, 10), "red") as image:
            image.save(output, "JPEG")
        self.source = output.getvalue()

    def list_albums(self):
        return [Album(id="album", name="Album")]

    def list_photos(self, album_id):
        assert album_id == "album"
        if self.fail:
            raise ProviderError("private provider details")
        return list(self.photos)

    def original(self, photo_id):
        return b"unsupported original", "image/heic"

    def thumbnail(self, photo_id):
        self.thumbnail_calls += 1
        return (self.source if self.preview_available else b"not ready"), "image/jpeg"

    def validate_connection(self):
        return "Connected"


def configured_service(tmp_path):
    provider = RecoveringProvider()
    repository = SettingsRepository(tmp_path)
    secrets = SecretStore(tmp_path)
    runtime = Runtime(repository, secrets, lambda _kind: provider)

    def configure(settings):
        settings.frame.album_id = "album"
        settings.frame.album_name = "Album"
        settings.frame.schedule_anchor = datetime(2026, 1, 1, tzinfo=UTC)
        settings.refresh_status.last_rendered_photo_id = "one"
        settings.refresh_status.last_attempted_schedule_key = "test-slot"
        settings.refresh_status.last_completed_schedule_key = "test-slot"

    repository.update(configure)
    runtime.photos = list(provider.photos)
    return ConfigurationService(repository, secrets, runtime, tmp_path), runtime, provider


def test_manual_refresh_recovers_negative_decode_verdict_without_render_or_schedule_change(
    tmp_path,
):
    service, runtime, provider = configured_service(tmp_path)
    before = runtime.repository.load()
    assert not runtime.photo_eligibility(before.frame).eligible
    assert provider.thumbnail_calls == 1
    provider.preview_available = True
    # Ordinary reads retain the cached result until the explicit recovery action.
    assert not runtime.photo_eligibility(before.frame).eligible
    assert provider.thumbnail_calls == 1
    runtime.set_preview("one")

    notice = service.refresh_current_album()

    after = runtime.repository.load()
    assert "1 available" in notice
    assert runtime.photo_eligibility(after.frame).eligible == provider.photos
    assert provider.thumbnail_calls == 2
    assert after.frame == before.frame
    assert after.refresh_status.last_rendered_photo_id == "one"
    assert after.refresh_status.last_attempted_schedule_key == "test-slot"
    assert after.refresh_status.last_completed_schedule_key == "test-slot"
    assert after.refresh_status.last_success_at is not None
    assert after.refresh_status.next_attempt_at is not None
    assert after.refresh_status.next_attempt_at > after.refresh_status.last_success_at
    assert runtime.preview_id() == "one"
    assert runtime.preserved_display_photo() == provider.photos[0]
    assert not runtime.renderer.snapshot().active
    assert runtime.prepared_image is None


def test_manual_refresh_failure_keeps_catalog_preview_settings_and_releases_claim(tmp_path):
    service, runtime, provider = configured_service(tmp_path)
    before = runtime.repository.load()
    runtime.set_preview("one")
    provider.fail = True

    with pytest.raises(RuntimeError, match="current photos are unchanged") as failure:
        service.refresh_current_album()

    assert "private provider details" not in str(failure.value)
    assert runtime.repository.load() == before
    assert runtime.catalog_snapshot()[1] == provider.photos
    assert runtime.preview_id() == "one"
    provider.fail = False
    provider.preview_available = True
    assert "1 available" in service.refresh_current_album()


def test_manual_refresh_removes_deleted_preview_and_retains_display_and_shuffle_order(tmp_path):
    service, runtime, provider = configured_service(tmp_path)
    displayed = provider.photos[0]
    runtime.set_preview("one")

    def shuffle(settings):
        settings.frame.photo_order = PhotoOrder.SHUFFLE
        settings.frame.shuffle_seed = 42
        settings.frame.shuffle_photo_ids = ["one", "two", "three"]
        settings.frame.starting_photo_id = "one"

    runtime.repository.update(shuffle)
    before = runtime.repository.load()
    provider.photos = [
        Photo(id=photo_id, filename=f"{photo_id}.jpg", width=20, height=10)
        for photo_id in ("three", "two", "four")
    ]
    provider.preview_available = True

    service.refresh_current_album()

    after = runtime.repository.load()
    assert runtime.catalog_snapshot()[1] == provider.photos
    assert runtime.preview_id() is None
    assert runtime.preserved_display_photo() == displayed
    assert after.frame.starting_photo_id is None
    assert after.frame.shuffle_photo_ids == ["two", "three", "four"]
    assert after.frame.shuffle_seed == before.frame.shuffle_seed
    assert after.frame.schedule_anchor == before.frame.schedule_anchor


def test_manual_refresh_excludes_other_changes_and_scheduled_work(tmp_path, monkeypatch):
    service, runtime, provider = configured_service(tmp_path)
    provider.preview_available = True
    entered, release = Event(), Event()
    original = provider.list_photos

    def blocked_list(album_id):
        entered.set()
        assert release.wait(5)
        return original(album_id)

    monkeypatch.setattr(provider, "list_photos", blocked_list)
    with ThreadPoolExecutor(max_workers=1) as executor:
        refresh = executor.submit(service.refresh_current_album)
        try:
            assert entered.wait(5)
            assert not runtime.refresh_lifecycle()
            with (
                pytest.raises(ValueError, match="album change"),
                runtime.interactive_operation(),
            ):
                pytest.fail("Mutation admitted during refresh")
            assert not runtime.maintenance_gate.enter_and_drain(0)
        finally:
            release.set()
        refresh.result(5)
    assert runtime.maintenance_gate.enter_and_drain(0.2)
    runtime.maintenance_gate.cancel()


def test_manual_refresh_refuses_maintenance_or_no_album(tmp_path):
    service, runtime, _provider = configured_service(tmp_path)
    assert runtime.maintenance_gate.enter_and_drain(0)
    with pytest.raises(MaintenanceError):
        service.refresh_current_album()
    runtime.maintenance_gate.cancel()
    runtime.repository.update(lambda settings: setattr(settings.frame, "album_id", None))
    with pytest.raises(ValueError, match="Choose an album"):
        service.refresh_current_album()
    # A rejected refresh must release the operation claim as well.
    with runtime.interactive_operation():
        pass
