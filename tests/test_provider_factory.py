from pathlib import Path

import httpx
import pytest
from pydantic import HttpUrl

from photoframe.models import AppSettings, ProviderKind
from photoframe.providers import ConfiguredProviderResolver, ImmichProvider, ProviderError
from photoframe.settings import SecretStore, SettingsRepository


def test_configured_resolver_builds_immich_from_its_own_configuration(tmp_path: Path, monkeypatch):
    repository = SettingsRepository(tmp_path)
    settings = AppSettings()
    settings.provider.server_url = HttpUrl("https://immich.test/photos")
    repository.save(settings)
    secrets = SecretStore(tmp_path)
    secrets.set_api_key("secret")

    provider = ConfiguredProviderResolver(repository, secrets)(ProviderKind.IMMICH)

    assert isinstance(provider, ImmichProvider)
    assert provider.base_url == "https://immich.test/photos/api"
    clients = []
    client_type = httpx.Client

    def handler(request):
        assert request.headers["x-api-key"] == "secret"
        assert request.url.path == "/photos/api/albums"
        return httpx.Response(200, json=[])

    def tracked_client(**kwargs):
        client = client_type(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("photoframe.providers.immich.httpx.Client", tracked_client)
    assert provider.list_albums() == []
    assert len(clients) == 1 and clients[0].is_closed


@pytest.mark.parametrize("server_url,api_key", [(None, "secret"), ("https://immich.test", None)])
def test_configured_resolver_rejects_incomplete_immich_configuration(
    tmp_path: Path, server_url: str | None, api_key: str | None
):
    repository = SettingsRepository(tmp_path)
    settings = AppSettings()
    settings.provider.server_url = HttpUrl(server_url) if server_url else None
    repository.save(settings)
    secrets = SecretStore(tmp_path)
    if api_key:
        secrets.set_api_key(api_key)

    with pytest.raises(ProviderError, match="Save an Immich server URL and API key first"):
        ConfiguredProviderResolver(repository, secrets)(ProviderKind.IMMICH)
