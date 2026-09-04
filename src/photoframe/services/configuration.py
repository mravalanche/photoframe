"""Configuration mutation and user-driven application workflows."""

from __future__ import annotations

import secrets as secure_random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol

from pydantic import HttpUrl

from ..image_processing import ImageProcessingError
from ..models import (
    AppSettings,
    DisplayDriver,
    NetworkSettings,
    Orientation,
    PhotoOrder,
    ProviderKind,
)
from ..renderer import RenderPhase, RenderState
from ..selector import active_selection, shuffled_photo_ids
from ..settings import SecretStore, SettingsRepository
from ..tls import tls_paths
from .runtime import Runtime


class ConnectionInput(Protocol):
    @property
    def server_url(self) -> str: ...

    @property
    def api_key(self) -> str: ...


class WorkflowInput(Protocol):
    @property
    def orientation(self) -> Orientation: ...

    @property
    def rotation_seconds(self) -> int: ...

    @property
    def photo_order(self) -> PhotoOrder | None: ...

    @property
    def timezone(self) -> str | None: ...

    @property
    def expected_refresh_seconds(self) -> int: ...

    @property
    def render_timeout_seconds(self) -> int: ...

    @property
    def display_driver(self) -> DisplayDriver: ...

    @property
    def display_model(self) -> str | None: ...

    @property
    def display_size(self) -> tuple[int, int] | None: ...


class NetworkInput(Protocol):
    @property
    def candidate(self) -> NetworkSettings: ...

    @property
    def confirmed(self) -> bool: ...


@dataclass(frozen=True)
class ServiceResult:
    message: str
    restart_required: bool = False


class ResetIncompleteError(RuntimeError):
    """Configuration reset succeeded but local cache cleanup did not."""


class ConfigurationService:
    COMPLETED_RENDER_RELEVANCE_SECONDS = 30
    COMPLETED_RENDER_ACKNOWLEDGEMENT_SECONDS = 6

    def __init__(
        self,
        repository: SettingsRepository,
        secrets: SecretStore,
        runtime: Runtime,
        data_dir: Path,
    ) -> None:
        self.repository = repository
        self.secrets = secrets
        self.runtime = runtime
        self.data_dir = data_dir
        self._reset_lock = Lock()
        self._album_selection_lock = Lock()

    def save_connection(self, form: ConnectionInput) -> str:
        try:

            def connection_change(settings: AppSettings) -> None:
                settings.provider.kind = ProviderKind.IMMICH
                settings.provider.server_url = HttpUrl(form.server_url)

            self.repository.update(connection_change)
            if form.api_key:
                self.secrets.set_api_key(form.api_key)
            message = self.runtime.provider().validate_connection()
            checked_at = datetime.now(UTC)

            def record_success(settings: AppSettings) -> None:
                settings.verification.ok = True
                settings.verification.message = message
                settings.verification.last_checked_at = checked_at

            self.repository.update(record_success)
            self.runtime.refresh_albums()
            self.runtime.clear_photos()
            return message
        except Exception as exc:
            checked_at = datetime.now(UTC)
            message = str(exc)

            def record_failure(settings: AppSettings) -> None:
                settings.verification.ok = False
                settings.verification.message = message
                settings.verification.last_checked_at = checked_at

            self.repository.update(record_failure)
            raise

    def refresh_albums(self) -> str:
        albums = self.runtime.refresh_albums()
        return f"Loaded {len(albums)} albums"

    def select_album(self, album_id: str) -> str:
        with self._album_selection_lock:
            return self._select_album(album_id)

    def _select_album(self, album_id: str) -> str:
        albums, _photos = self.runtime.catalog_snapshot()
        album = next((item for item in albums if item.id == album_id), None)
        if not album:
            raise ValueError("Choose an album from the loaded list")

        previous = self.repository.load()
        rendered_before = (
            self.runtime.photo(self.runtime.renderer.rendered_photo_id())
            or self.runtime.preserved_display_photo()
        )
        eligible_before = self.runtime.photo_eligibility(previous.frame, _photos).eligible
        displayed_before = (
            rendered_before
            or active_selection(eligible_before, previous.frame, datetime.now(UTC)).photo
        )
        candidate_frame = previous.frame.model_copy(deep=True)
        candidate_frame.album_id = album.id
        candidate_frame.album_name = album.name
        try:
            photos = self.runtime.provider().list_photos(album.id)
            eligible = self.runtime.photo_eligibility(candidate_frame, photos).eligible
        except Exception as exc:
            raise RuntimeError(
                f"Could not use {album.name}. Your current album is unchanged; "
                "check the photo source and try again."
            ) from exc

        def choose_album(settings: AppSettings) -> None:
            settings.frame.album_id = album.id
            settings.frame.album_name = album.name
            if settings.frame.starting_photo_id not in {photo.id for photo in eligible}:
                settings.frame.starting_photo_id = None
            if settings.frame.photo_order == PhotoOrder.SHUFFLE:
                settings.frame.shuffle_photo_ids = shuffled_photo_ids(eligible, settings.frame)
            settings.refresh_status.next_attempt_at = None

        self.repository.update(choose_album)
        self.runtime.preserve_display_photo(displayed_before)
        self.runtime.replace_photos(photos)
        self.runtime.set_preview(None)
        return f"Selected {album.name}; found {len(photos)} images"

    def save_workflow(self, form: WorkflowInput) -> str:
        _albums, photos = self.runtime.catalog_snapshot()
        anchor = datetime.now(UTC)

        def workflow_change(settings: AppSettings) -> None:
            previous_order = settings.frame.photo_order
            settings.frame.orientation = form.orientation
            settings.frame.rotation_seconds = form.rotation_seconds
            settings.frame.photo_order = form.photo_order or settings.frame.photo_order
            settings.device.timezone = form.timezone or settings.device.timezone
            settings.device.expected_refresh_seconds = form.expected_refresh_seconds
            settings.device.render_timeout_seconds = form.render_timeout_seconds
            settings.device.display_driver = form.display_driver
            settings.device.display_model = form.display_model
            if form.display_size:
                settings.device.set_display_size(*form.display_size)
            else:
                settings.device.set_display_size(None, None)
            settings.frame.schedule_anchor = anchor
            if not any(
                photo.id == settings.frame.starting_photo_id
                and photo.matches(settings.frame.orientation)
                for photo in photos
            ):
                settings.frame.starting_photo_id = None
            if settings.frame.photo_order == PhotoOrder.ALBUM:
                settings.frame.shuffle_photo_ids = []
                settings.frame.shuffle_seed = 0
            elif previous_order != PhotoOrder.SHUFFLE:
                settings.frame.shuffle_seed = secure_random.randbits(63)
                settings.frame.shuffle_photo_ids = []

        self.repository.update(workflow_change)
        saved_frame = self.repository.load().frame
        eligible_ids = {
            photo.id for photo in self.runtime.photo_eligibility(saved_frame, photos).eligible
        }
        if saved_frame.starting_photo_id not in eligible_ids:
            self.repository.update(lambda saved: setattr(saved.frame, "starting_photo_id", None))
        if self.runtime.preview_id() not in eligible_ids:
            self.runtime.set_preview(None)
        self.runtime.reconcile_shuffle()
        self.runtime.initialise_display()
        return "Frame settings saved; rotation restarted from now"

    def save_network(self, form: NetworkInput, *, can_restart: bool) -> ServiceResult:
        candidate = form.candidate
        current = self.repository.load().network
        listener_changed = candidate != current
        if listener_changed and not form.confirmed:
            raise ValueError(
                "Confirm the listener change before saving; Photoframe must restart and the "
                f"new address will be {candidate.display_address}"
            )
        tls_paths(self.data_dir, candidate)
        self.repository.update(lambda settings: setattr(settings, "network", candidate))
        if not listener_changed:
            return ServiceResult("Network settings are already up to date")
        if can_restart:
            return ServiceResult(
                "Network settings saved. Photoframe is restarting; open "
                f"{candidate.display_address} when it is ready.",
                restart_required=True,
            )
        return ServiceResult(
            f"Network settings saved. Restart Photoframe, then open {candidate.display_address}."
        )

    def preview_photo(self, photo_id: str) -> None:
        self._require_eligible(photo_id)
        self.runtime.set_preview(photo_id)
        self.runtime.renderer.reset()

    def clear_preview(self) -> None:
        self.runtime.set_preview(None)
        self.runtime.renderer.reset()

    def start_from_photo(self, photo_id: str) -> str:
        settings = self.repository.load()
        self._require_eligible(photo_id)
        anchor = datetime.now(UTC)

        def save_start(saved: AppSettings) -> None:
            saved.frame.starting_photo_id = photo_id
            saved.frame.schedule_anchor = anchor

        self.repository.update(save_start)
        if settings.frame.photo_order == PhotoOrder.SHUFFLE:
            self.runtime.reconcile_shuffle(anchor_id=photo_id, fresh=True)
            return "A new shuffled round now starts from the selected image"
        return "Rotation now starts from the selected image"

    def reset(self, *, can_restart: bool) -> ServiceResult:
        if not self._reset_lock.acquire(blocking=False):
            raise ValueError("Photoframe reset is already in progress")
        try:
            listener_changed = self.repository.load().network != NetworkSettings()
            if self.runtime.reset_to_defaults():
                raise ResetIncompleteError(
                    "Reset incomplete. Your configuration was cleared, but some local photo "
                    "data could not be removed. Retry cleanup before reconnecting."
                )
            if listener_changed and can_restart:
                return ServiceResult(
                    "Photoframe was reset to defaults and is restarting at http://127.0.0.1:8000.",
                    restart_required=True,
                )
            return ServiceResult(
                "Photoframe was reset to defaults. Connect a photo provider to begin."
            )
        finally:
            self._reset_lock.release()

    def start_render(self, operation_id: str | None = None) -> None:
        photo = self.runtime.photo(self.runtime.preview_id())
        if not photo:
            raise ValueError("Select an image preview before sending it to the frame")
        try:
            if self.runtime.has_display():
                self.runtime.render_service.start(photo.id, operation_id=operation_id)
            else:
                self.runtime.prepare_photo(photo.id)
                self.runtime.renderer.start(photo.id, operation_id=operation_id)
        except ImageProcessingError:
            self.runtime.set_preview(None)
            raise

    def reset_render(self) -> None:
        self.runtime.renderer.reset()
        self.runtime.set_preview(None)

    def dismiss_render(self) -> None:
        if self.runtime.renderer.snapshot().phase == RenderPhase.COMPLETE:
            self.reset_render()

    def current_render_state(self, now: datetime | None = None) -> RenderState:
        """Return current state, expiring only stale successful acknowledgements."""
        current = now or datetime.now(UTC)
        state = self.runtime.renderer.update(self.repository.load().device, current)
        if (
            state.phase == RenderPhase.COMPLETE
            and state.finished_at
            and (current - state.finished_at).total_seconds()
            >= self.COMPLETED_RENDER_RELEVANCE_SECONDS
        ):
            self.reset_render()
            return self.runtime.renderer.snapshot()
        return state

    def _require_eligible(self, photo_id: str) -> None:
        settings = self.repository.load()
        _albums, photos = self.runtime.catalog_snapshot()
        eligible_ids = {
            photo.id for photo in self.runtime.photo_eligibility(settings.frame, photos).eligible
        }
        if photo_id not in eligible_ids:
            raise ValueError("That image is not eligible or cannot be decoded by this PhotoFrame")
