"""Provider-neutral construction from persisted application configuration."""

from __future__ import annotations

from collections.abc import Callable

from ..models import ProviderKind
from ..settings import SecretStore, SettingsRepository
from .base import PhotoProvider, ProviderError
from .immich import ImmichProvider

ProviderResolver = Callable[[ProviderKind], PhotoProvider]


class ConfiguredProviderResolver:
    """Resolve a provider kind while keeping backend configuration backend-specific."""

    def __init__(self, settings: SettingsRepository, secrets: SecretStore) -> None:
        self.settings = settings
        self.secrets = secrets

    def __call__(self, kind: ProviderKind) -> PhotoProvider:
        if kind == ProviderKind.IMMICH:
            configuration = self.settings.load().provider
            api_key = self.secrets.get_api_key()
            if not configuration.server_url or not api_key:
                raise ProviderError("Save an Immich server URL and API key first")
            return ImmichProvider(str(configuration.server_url), api_key)
        raise ProviderError(f"Unsupported photo provider: {kind}")
