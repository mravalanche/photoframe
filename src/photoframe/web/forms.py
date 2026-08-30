"""Typed parsing for Photoframe's HTML form inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..models import (
    CertificateMode,
    DisplayDriver,
    NetworkAccess,
    NetworkSettings,
    Orientation,
    PhotoOrder,
    WebProtocol,
)


def _text(form: Mapping[str, object], key: str, default: str = "") -> str:
    return str(form.get(key, default)).strip()


@dataclass(frozen=True)
class ConnectionForm:
    server_url: str
    api_key: str

    @classmethod
    def parse(cls, form: Mapping[str, object]) -> ConnectionForm:
        return cls(_text(form, "server_url"), _text(form, "api_key"))


@dataclass(frozen=True)
class AlbumForm:
    album_id: str

    @classmethod
    def parse(cls, form: Mapping[str, object]) -> AlbumForm:
        return cls(_text(form, "album_id"))


@dataclass(frozen=True)
class PhotoForm:
    photo_id: str

    @classmethod
    def parse(cls, form: Mapping[str, object]) -> PhotoForm:
        return cls(_text(form, "photo_id"))


@dataclass(frozen=True)
class WorkflowForm:
    orientation: Orientation
    rotation_seconds: int
    photo_order: PhotoOrder | None
    timezone: str | None
    expected_refresh_seconds: int
    render_timeout_seconds: int
    display_driver: DisplayDriver
    display_model: str | None
    display_size: tuple[int, int] | None

    @classmethod
    def parse(cls, form: Mapping[str, object]) -> WorkflowForm:
        width = _text(form, "display_width_px")
        height = _text(form, "display_height_px")
        if bool(width) != bool(height):
            raise ValueError("Enter both native display dimensions, or leave both blank")
        order = _text(form, "photo_order")
        timezone = _text(form, "timezone")
        model = _text(form, "display_model")
        return cls(
            orientation=Orientation(_text(form, "orientation")),
            rotation_seconds=int(_text(form, "rotation_seconds")),
            photo_order=PhotoOrder(order) if order else None,
            timezone=timezone or None,
            expected_refresh_seconds=int(_text(form, "expected_refresh_seconds")),
            render_timeout_seconds=int(_text(form, "render_timeout_seconds")),
            display_driver=DisplayDriver(_text(form, "display_driver", "auto")),
            display_model=model or None,
            display_size=(int(width), int(height)) if width else None,
        )


@dataclass(frozen=True)
class NetworkForm:
    candidate: NetworkSettings
    confirmed: bool

    @classmethod
    def parse(cls, form: Mapping[str, object]) -> NetworkForm:
        raw_port = _text(form, "network_port")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("Listening port must be a whole number from 1 to 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Listening port must be between 1 and 65535")
        return cls(
            candidate=NetworkSettings(
                access=NetworkAccess(_text(form, "network_access", "device_only")),
                port=port,
                protocol=WebProtocol(_text(form, "web_protocol", "http")),
                certificate_mode=CertificateMode(_text(form, "certificate_mode", "automatic")),
                certificate_path=_text(form, "certificate_path") or None,
                private_key_path=_text(form, "private_key_path") or None,
            ),
            confirmed=_text(form, "confirm_endpoint_change") == "yes",
        )
