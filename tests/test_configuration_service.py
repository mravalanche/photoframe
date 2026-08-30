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
