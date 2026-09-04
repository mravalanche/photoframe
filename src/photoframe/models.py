from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class Orientation(StrEnum):
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"


class PhotoOrder(StrEnum):
    ALBUM = "album"
    SHUFFLE = "shuffle"


class ScheduleMode(StrEnum):
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"


class ProviderKind(StrEnum):
    IMMICH = "immich"


class DisplayDriver(StrEnum):
    """The hardware backend used to present prepared images."""

    AUTO = "auto"
    INKY = "inky"
    MOCK = "mock"


class NetworkAccess(StrEnum):
    DEVICE_ONLY = "device_only"
    LOCAL_NETWORK = "local_network"

    @property
    def bind_address(self) -> str:
        # The wildcard is only reachable after an explicit local-network
        # choice.
        return "127.0.0.1" if self == self.DEVICE_ONLY else "0.0.0.0"  # nosec B104


class WebProtocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"


class CertificateMode(StrEnum):
    AUTOMATIC = "automatic"
    SUPPLIED = "supplied"


class ProviderSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    kind: ProviderKind = ProviderKind.IMMICH
    server_url: HttpUrl | None = None


class FrameSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    orientation: Orientation = Orientation.LANDSCAPE
    rotation_seconds: Annotated[int, Field(ge=30, le=2_592_000)] = 3600
    schedule_mode: ScheduleMode = ScheduleMode.DAILY
    daily_time: str = "03:00"
    weekly_day: Annotated[int, Field(ge=0, le=6)] = 0
    weekly_time: str = "03:00"
    album_id: str | None = None
    album_name: str | None = None
    schedule_anchor: datetime = Field(default_factory=lambda: datetime.now(UTC))
    starting_photo_id: str | None = None
    photo_order: PhotoOrder = PhotoOrder.ALBUM
    shuffle_seed: int = 0
    shuffle_photo_ids: list[str] = Field(default_factory=list)

    @field_validator("daily_time", "weekly_time")
    @classmethod
    def valid_local_time(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Use a 24-hour time in HH:MM format") from exc
        return parsed.strftime("%H:%M")


class DeviceSettings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    timezone: str = "Europe/London"
    clock_24_hour: bool = True
    expected_refresh_seconds: Annotated[int, Field(ge=5, le=300)] = 28
    render_timeout_seconds: Annotated[int, Field(ge=10, le=600)] = 90
    display_width_px: Annotated[int | None, Field(ge=1, le=16_384)] = None
    display_height_px: Annotated[int | None, Field(ge=1, le=16_384)] = None
    display_driver: DisplayDriver = DisplayDriver.AUTO
    # A label selected by the user or returned by Inky. It is informational: Inky
    # itself remains the authority for the connected panel's capabilities.
    display_model: str | None = None
    display_detected: bool = False
    display_status: str = "Display not checked"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def complete_display_size(self) -> "DeviceSettings":
        if (self.display_width_px is None) != (self.display_height_px is None):
            raise ValueError("Set both display width and display height, or neither")
        return self

    @property
    def display_size(self) -> tuple[int, int] | None:
        """Native display size, when the frame has been configured."""
        if self.display_width_px is None or self.display_height_px is None:
            return None
        return self.display_width_px, self.display_height_px

    def set_display_size(self, width: int | None, height: int | None) -> None:
        """Validate and update the paired native dimensions atomically."""
        candidate = type(self).model_validate(
            {
                **self.model_dump(),
                "display_width_px": width,
                "display_height_px": height,
            }
        )
        object.__setattr__(self, "display_width_px", candidate.display_width_px)
        object.__setattr__(self, "display_height_px", candidate.display_height_px)


class RefreshSettings(BaseModel):
    """Bounded, local-first settings for the unattended refresh worker."""

    model_config = ConfigDict(validate_assignment=True)

    # Four GiB is deliberately generous for a Pi with a normal SD card or SSD,
    # while remaining editable for smaller installations.
    cache_max_bytes: Annotated[int, Field(ge=100 * 1024 * 1024)] = 4 * 1024**3
    cache_prefetch_count: Annotated[int, Field(ge=1, le=500)] = 30
    catalog_refresh_seconds: Annotated[int, Field(ge=60, le=2_592_000)] = 3600
    retry_seconds: Annotated[int, Field(ge=30, le=86_400)] = 300
    health_stale_seconds: Annotated[int, Field(ge=60, le=2_592_000)] = 7200


class NetworkSettings(BaseModel):
    """Secure-default web listener settings persisted with the installation."""

    model_config = ConfigDict(validate_assignment=True)

    access: NetworkAccess = NetworkAccess.DEVICE_ONLY
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    protocol: WebProtocol = WebProtocol.HTTP
    certificate_mode: CertificateMode = CertificateMode.AUTOMATIC
    certificate_path: str | None = None
    private_key_path: str | None = None

    @field_validator("access", mode="before")
    @classmethod
    def normalize_legacy_access(cls, value: object) -> object:
        """Accept the former persisted value while writing only the current name."""
        return NetworkAccess.LOCAL_NETWORK if value == "home_network" else value

    @model_validator(mode="after")
    def supplied_tls_paths_are_complete(self) -> "NetworkSettings":
        if (
            self.protocol == WebProtocol.HTTPS
            and self.certificate_mode == CertificateMode.SUPPLIED
            and (not self.certificate_path or not self.private_key_path)
        ):
            raise ValueError("Choose both a certificate file and private-key file")
        return self

    @property
    def bind_address(self) -> str:
        return self.access.bind_address

    @property
    def display_address(self) -> str:
        host = "127.0.0.1" if self.access == NetworkAccess.DEVICE_ONLY else "<device-ip>"
        return f"{self.protocol.value}://{host}:{self.port}"


class RefreshStatus(BaseModel):
    """Persisted outcome of the last unattended refresh attempt."""

    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_attempt_at: datetime | None = None
    consecutive_failures: Annotated[int, Field(ge=0)] = 0
    last_error: str | None = None
    cached_photo_count: Annotated[int, Field(ge=0)] = 0
    cached_bytes: Annotated[int, Field(ge=0)] = 0
    last_completed_schedule_slot: int | None = None
    last_completed_schedule_anchor: datetime | None = None
    last_completed_schedule_key: str | None = None
    last_attempted_schedule_key: str | None = None
    last_rendered_photo_id: str | None = None
    last_render_error: str | None = None


class Verification(BaseModel):
    last_checked_at: datetime | None = None
    ok: bool = False
    message: str = "Connection not checked"


class AppSettings(BaseModel):
    schema_version: int = 2
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    frame: FrameSettings = Field(default_factory=FrameSettings)
    device: DeviceSettings = Field(default_factory=DeviceSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    refresh: RefreshSettings = Field(default_factory=RefreshSettings)
    refresh_status: RefreshStatus = Field(default_factory=RefreshStatus)
    verification: Verification = Field(default_factory=Verification)


class Album(BaseModel):
    id: str
    name: str
    asset_count: int = 0
    thumbnail_asset_id: str | None = None


class Photo(BaseModel):
    id: str
    filename: str
    width: int | None = None
    height: int | None = None
    taken_at: datetime | None = None

    def matches(self, orientation: Orientation) -> bool:
        if not self.width or not self.height or self.width == self.height:
            return False
        return (
            self.width > self.height
            if orientation == Orientation.LANDSCAPE
            else self.height > self.width
        )


class ActiveSelection(BaseModel):
    photo: Photo | None
    eligible_count: int
    position: int | None = None
    next_change_at: datetime | None = None
