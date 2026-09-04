from datetime import UTC, datetime
from pathlib import Path

import pytest

from photoframe.models import Album, Photo, ProviderKind
from photoframe.providers import ProviderError
from photoframe.services.configuration import ConfigurationService
from photoframe.services.runtime import Runtime
from photoframe.settings import SecretStore, SettingsRepository
from photoframe.web.forms import ConnectionForm


class ServiceProvider:
    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure

    def validate_connection(self) -> str:
        if self.failure:
            raise ProviderError(self.failure)
        return "Connected through service"

    def list_albums(self) -> list[Album]:
        return [Album(id="album", name="Service album")]

    def list_photos(self, album_id: str) -> list[Photo]:
        del album_id
        return []

    def thumbnail(self, photo_id: str) -> tuple[bytes, str]:
        del photo_id
        return b"image", "image/jpeg"

    def original(self, photo_id: str) -> tuple[bytes, str]:
        del photo_id
        return b"image", "image/jpeg"


def service_for(tmp_path: Path, provider: ServiceProvider) -> tuple[ConfigurationService, Runtime]:
    repository = SettingsRepository(tmp_path)
    secrets = SecretStore(tmp_path)
    runtime = Runtime(repository, secrets, lambda _kind: provider)
    return ConfigurationService(repository, secrets, runtime, tmp_path), runtime


def test_connection_service_persists_verification_and_clears_stale_photos(tmp_path: Path):
    service, runtime = service_for(tmp_path, ServiceProvider())
    runtime.photos = [Photo(id="stale", filename="stale.jpg")]

    message = service.save_connection(ConnectionForm("https://immich.test", "secret"))

    saved = runtime.repository.load()
    assert message == "Connected through service"
    assert saved.provider.kind == ProviderKind.IMMICH
    assert str(saved.provider.server_url) == "https://immich.test/"
    assert saved.verification.ok
    assert saved.verification.message == message
    assert runtime.catalog_snapshot() == ([Album(id="album", name="Service album")], [])


def test_connection_service_records_provider_failure(tmp_path: Path):
    service, runtime = service_for(tmp_path, ServiceProvider(failure="provider offline"))

    with pytest.raises(ProviderError, match="provider offline"):
        service.save_connection(ConnectionForm("https://immich.test", "secret"))

    verification = runtime.repository.load().verification
    assert not verification.ok
    assert verification.message == "provider offline"
    assert verification.last_checked_at is not None


class AlbumProvider(ServiceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.photo_calls: list[str] = []
        self.fail_album: str | None = None

    def list_albums(self) -> list[Album]:
        return [Album(id="current", name="Current album"), Album(id="new", name="New album")]

    def list_photos(self, album_id: str) -> list[Photo]:
        self.photo_calls.append(album_id)
        if album_id == self.fail_album:
            raise ProviderError("catalog unavailable")
        return [Photo(id=f"{album_id}-photo", filename=f"{album_id}.jpg")]


def test_select_album_commits_catalog_without_restarting_schedule_or_render(tmp_path: Path):
    provider = AlbumProvider()
    service, runtime = service_for(tmp_path, provider)
    runtime.albums = provider.list_albums()
    anchor = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    def configure_current(settings):
        settings.frame.album_id = "current"
        settings.frame.album_name = "Current album"
        settings.frame.schedule_anchor = anchor

    runtime.repository.update(configure_current)
    runtime.photos = [Photo(id="current-photo", filename="current.jpg")]
    runtime.renderer.last_rendered_photo_id = "current-photo"

    message = service.select_album("new")

    saved = runtime.repository.load()
    assert message == "Selected New album; found 1 images"
    assert provider.photo_calls == ["new"]
    assert saved.frame.album_id == "new"
    assert saved.frame.album_name == "New album"
    assert saved.frame.schedule_anchor == anchor
    assert runtime.catalog_snapshot()[1] == [Photo(id="new-photo", filename="new.jpg")]
    assert runtime.renderer.rendered_photo_id() == "current-photo"
    assert runtime.preserved_display_photo() == Photo(id="current-photo", filename="current.jpg")


def test_select_album_failure_preserves_current_settings_catalog_preview_and_render(tmp_path: Path):
    provider = AlbumProvider()
    provider.fail_album = "new"
    service, runtime = service_for(tmp_path, provider)
    runtime.albums = provider.list_albums()
    runtime.photos = [Photo(id="current-photo", filename="current.jpg")]
    runtime.set_preview("current-photo")
    runtime.renderer.last_rendered_photo_id = "current-photo"

    def configure_current(settings):
        settings.frame.album_id = "current"
        settings.frame.album_name = "Current album"

    runtime.repository.update(configure_current)
    before = runtime.repository.load()

    with pytest.raises(RuntimeError, match="current album is unchanged"):
        service.select_album("new")

    assert runtime.repository.load() == before
    assert runtime.catalog_snapshot()[1] == [Photo(id="current-photo", filename="current.jpg")]
    assert runtime.preview_id() == "current-photo"
    assert runtime.renderer.rendered_photo_id() == "current-photo"
    assert runtime.preserved_display_photo() is None


def test_select_album_rejects_missing_pending_album_without_mutation(tmp_path: Path):
    provider = AlbumProvider()
    service, runtime = service_for(tmp_path, provider)
    runtime.albums = provider.list_albums()
    before = runtime.repository.load()

    with pytest.raises(ValueError, match="loaded list"):
        service.select_album("missing")

    assert runtime.repository.load() == before
    assert provider.photo_calls == []
