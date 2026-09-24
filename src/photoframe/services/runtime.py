"""Application runtime orchestration independent of FastAPI."""

from __future__ import annotations

import secrets as secure_random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, RLock
from time import monotonic

from PIL import Image

from ..cache import CacheStats, PhotoCache
from ..display import InkyDisplay, apply_profile, discover_inky
from ..image_processing import ImageProcessingError, image_is_decodable, prepare_for_display
from ..lifecycle import RefreshCoordinator
from ..models import Album, AppSettings, FrameSettings, Photo, PhotoOrder
from ..providers import PhotoProvider, ProviderError, ProviderResolver
from ..renderer import MockRenderCoordinator, RenderService
from ..schedule import catch_up_occurrence
from ..selector import (
    EligibilitySummary,
    active_selection,
    classify_photos,
    next_photo,
    shuffled_photo_ids,
)
from ..settings import SecretStore, SettingsRepository
from ..updater.maintenance import MaintenanceError, MaintenanceGate
from .album_load import AlbumLoad, AlbumLoadCancelled


class Runtime:
    def __init__(
        self,
        settings: SettingsRepository,
        secrets: SecretStore,
        provider_resolver: ProviderResolver,
        demo_provider: PhotoProvider | None = None,
        maintenance_gate: MaintenanceGate | None = None,
    ):
        self.repository, self.secrets = settings, secrets
        self.provider_resolver = provider_resolver
        self.demo_provider = demo_provider
        self.maintenance_gate = maintenance_gate or MaintenanceGate()
        self.albums: list[Album] = []
        self.photos: list[Photo] = []
        self._loaded = False
        self.selected_preview_id: str | None = None
        self._preserved_display_photo: Photo | None = None
        self._prepared_image: Image.Image | None = None
        self.renderer = MockRenderCoordinator()
        self.display: InkyDisplay | None = None
        self.cache = PhotoCache(settings.data_dir, self.repository.load().refresh.cache_max_bytes)
        self.refresh_coordinator = RefreshCoordinator(self)
        self._runtime_lock = RLock()
        self._library_load_lock = Lock()
        self._library_load: AlbumLoad | None = None
        self._eligibility_lock = RLock()
        self._claim_lock = Lock()
        self._lifecycle_inflight = False
        self._album_change_inflight = False
        self._interactive_inflight = 0
        self._refresh_idle = Event()
        self._refresh_idle.set()
        self._startup_catalog_restore_pending = True
        self.render_service = RenderService(
            self.renderer,
            lambda photo_id: self.prepare_photo(photo_id),
            self._show_prepared,
        )

    def _show_prepared(self, image: Image.Image) -> None:
        with self._runtime_lock:
            display = self.display
        if not display:
            raise RuntimeError("No physical display is available")
        display.show(image)

    @property
    def loaded(self) -> bool:
        with self._runtime_lock:
            return self._loaded

    @loaded.setter
    def loaded(self, value: bool) -> None:
        with self._runtime_lock:
            self._loaded = value

    @property
    def prepared_image(self) -> Image.Image | None:
        with self._runtime_lock:
            return self._prepared_image

    def has_display(self) -> bool:
        with self._runtime_lock:
            return self.display is not None

    def initialise_display(self) -> None:
        """Probe Inky once at boot and retain its reported native capabilities."""
        settings = self.repository.load()
        display, profile = discover_inky(settings.device)
        with self._runtime_lock:
            self.display = display

        def persist(saved):
            saved.device.display_status = profile.status
            if display or saved.device.display_size:
                apply_profile(saved.device, profile)

        self.repository.update(persist)

    def provider(self) -> PhotoProvider:
        if self.demo_provider:
            return self.demo_provider
        return self.provider_resolver(self.repository.load().provider.kind)

    def refresh_albums(self) -> list[Album]:
        albums = self.provider().list_albums()
        with self._runtime_lock:
            self.albums = albums
            self.loaded = True
            return list(self.albums)

    def library_load_snapshot(self) -> dict:
        with self._runtime_lock:
            load = self._library_load
        return load.snapshot() if load else {"active": False, "phase": "idle"}

    def cancel_library_load(self, job_id: str | None = None) -> bool:
        with self._runtime_lock:
            load = self._library_load
        if load and (job_id is None or load.snapshot()["id"] == job_id):
            return load.cancel()
        return False

    def refresh_photos(self) -> list[Photo]:
        with self._library_load_lock:
            settings = self.repository.load()
            load = AlbumLoad(settings.frame.album_id or "", settings.frame.album_name)
            with self._claim_lock:
                if self._album_change_inflight:
                    raise AlbumLoadCancelled
                with self._runtime_lock:
                    self._library_load = load
            try:
                photos = (
                    self.provider().list_photos(settings.frame.album_id)
                    if settings.frame.album_id
                    else []
                )
                self.photo_eligibility(
                    settings.frame, photos, load.progress, checkpoint=load.checkpoint
                )
                with load.committing():
                    with self._runtime_lock:
                        self.photos = photos
                    self.reconcile_shuffle()
                load.finish("complete", "Your album is ready")
                return list(photos)
            except AlbumLoadCancelled:
                load.finish("cancelled", "Album loading cancelled")
                raise
            except Exception:
                load.finish(
                    "failed", "Photos could not be loaded. Check the photo source and try again."
                )
                raise

    def reconcile_shuffle(self, *, anchor_id: str | None = None, fresh: bool = False) -> None:
        """Persist a stable shuffled deck after settings or eligibility changes."""
        _albums, photos = self.catalog_snapshot()
        frame = self.repository.load().frame
        eligible = self.photo_eligibility(frame, photos).eligible

        def reconcile(settings):
            if settings.frame.photo_order != PhotoOrder.SHUFFLE:
                return
            if fresh:
                settings.frame.shuffle_seed = secure_random.randbits(63)
                settings.frame.shuffle_photo_ids = []
            settings.frame.shuffle_photo_ids = shuffled_photo_ids(
                eligible, settings.frame, anchor_id=anchor_id
            )

        self.repository.update(reconcile)

    def photo(self, photo_id: str | None) -> Photo | None:
        with self._runtime_lock:
            return next((photo for photo in self.photos if photo.id == photo_id), None)

    def catalog_snapshot(self) -> tuple[list[Album], list[Photo]]:
        with self._runtime_lock:
            return list(self.albums), list(self.photos)

    def workspace_snapshot(self) -> tuple[AppSettings, list[Album], list[Photo]]:
        """Read the selected album and its matching published catalog together."""
        with self._runtime_lock:
            return self.repository.load(), list(self.albums), list(self.photos)

    def commit_album(
        self,
        photos: list[Photo],
        displayed: Photo | None,
        change: Callable[[AppSettings], None],
        *,
        preview_id: str | None = None,
    ) -> None:
        prepared_catalog = list(photos)
        with self._runtime_lock:
            self.repository.update(change)
            self._preserved_display_photo = displayed
            self.photos = prepared_catalog
            self.selected_preview_id = preview_id

    def clear_photos(self) -> None:
        """Clear the loaded photo catalog without exposing runtime locking."""
        with self._runtime_lock:
            self.photos = []

    def replace_photos(self, photos: list[Photo]) -> None:
        """Publish a fully loaded catalog after its settings change has committed."""
        with self._runtime_lock:
            self.photos = list(photos)

    def preserve_display_photo(self, photo: Photo | None) -> None:
        """Keep representing the physical frame while its former catalog is replaced."""
        with self._runtime_lock:
            self._preserved_display_photo = photo

    def preserved_display_photo(self) -> Photo | None:
        with self._runtime_lock:
            return self._preserved_display_photo

    def preview_id(self) -> str | None:
        with self._runtime_lock:
            return self.selected_preview_id

    def set_preview(self, photo_id: str | None) -> None:
        with self._runtime_lock:
            self.selected_preview_id = photo_id

    def _cache_key(self, photo_id: str) -> str:
        return f"{self.repository.load().provider.kind}:{photo_id}"

    def photo_is_decodable(self, photo_id: str, *, retry_unsupported: bool = False) -> bool:
        """Verify an asset once, persisting the installed-pipeline verdict."""
        key = self._cache_key(photo_id)
        # Status requests for the current album must not wait behind a network
        # download being checked for a candidate album.
        known = self.cache.decodability(key)
        if known is not None and not (retry_unsupported and not known):
            return known
        with self._eligibility_lock:
            known = self.cache.decodability(key)
            if known is not None and not (retry_unsupported and not known):
                return known
            try:
                self.render_source(photo_id)
            except ImageProcessingError:
                supported = False
            else:
                supported = True
            self.cache.set_decodability(key, supported)
            return supported

    def render_source(self, photo_id: str) -> bytes:
        """Return bytes the installed renderer can decode, using a provider preview fallback."""
        source = self.cache_photo(photo_id)
        if image_is_decodable(source):
            return source

        # Immich commonly stores originals as HEIC while exposing a generated
        # JPEG preview. That preview is still a genuine render source for the
        # frame, so validate and cache it before excluding the asset.
        fallback, _media_type = self.provider().thumbnail(photo_id)
        if not image_is_decodable(fallback):
            raise ImageProcessingError("The selected photo could not be decoded")
        self.cache.put(self._cache_key(photo_id), fallback)
        return fallback

    def photo_eligibility(
        self,
        frame: FrameSettings,
        photos: list[Photo] | None = None,
        progress: Callable[[int, int], None] | None = None,
        *,
        retry_unsupported: bool = False,
        checkpoint: Callable[[], None] | None = None,
    ) -> EligibilitySummary:
        candidates = photos if photos is not None else self.catalog_snapshot()[1]
        total = sum(photo.matches(frame.orientation) for photo in candidates)
        checked = 0
        if progress:
            progress(checked, total)

        def check(photo: Photo) -> bool:
            nonlocal checked
            if checkpoint:
                checkpoint()
            supported = self.photo_is_decodable(photo.id, retry_unsupported=retry_unsupported)
            checked += 1
            if progress:
                progress(checked, total)
            return supported

        return classify_photos(
            candidates,
            frame,
            check,
        )

    def claim_album_change(self) -> None:
        """Exclude scheduled refresh/render while a candidate catalog is prepared."""
        with self._claim_lock:
            if self._album_change_inflight:
                raise ValueError("An album change is already in progress")
            if self._interactive_inflight:
                raise ValueError("Another change is being saved; try changing albums shortly")
            if self.renderer.snapshot().active or self.renderer.hardware_busy:
                raise ValueError("Wait for the current frame update before changing albums")
            self._album_change_inflight = True

    @contextmanager
    def interactive_operation(self) -> Iterator[None]:
        """Atomically admit web mutations against the background album claim."""
        with self._claim_lock:
            if self._album_change_inflight:
                raise ValueError("Wait for the album change to finish before making another change")
            self._interactive_inflight += 1
        try:
            yield
        finally:
            with self._claim_lock:
                self._interactive_inflight -= 1

    def wait_for_refresh(self, checkpoint: Callable[[], None] | None = None) -> None:
        deadline = monotonic() + 90
        while not self._refresh_idle.wait(timeout=0.1):
            if checkpoint:
                checkpoint()
            if checkpoint is None and monotonic() >= deadline:
                raise ValueError("The photo library is still refreshing; try again shortly")
        if checkpoint:
            checkpoint()
        if self.renderer.snapshot().active or self.renderer.hardware_busy:
            raise ValueError("Wait for the current frame update before changing albums")

    def release_album_change(self) -> None:
        with self._claim_lock:
            self._album_change_inflight = False

    def renderable_photos(self, photos: list[Photo], frame: FrameSettings) -> list[Photo]:
        return self.photo_eligibility(frame, photos).eligible

    def prepare_photo(self, photo_id: str) -> Image.Image:
        target_size = self.repository.load().device.display_size
        if not target_size:
            raise ImageProcessingError(
                "Set the frame's native display width and height before rendering"
            )
        try:
            source = self.render_source(photo_id)
            prepared = prepare_for_display(source, target_size)
        except ImageProcessingError:
            self.cache.set_decodability(self._cache_key(photo_id), False)
            raise
        with self._runtime_lock:
            self._prepared_image = prepared
        return prepared

    def cache_photo(self, photo_id: str) -> bytes:
        """Get an original locally first, then acquire and safely cache it."""
        settings = self.repository.load()
        self.cache.set_max_bytes(settings.refresh.cache_max_bytes)
        key = f"{settings.provider.kind}:{photo_id}"
        cached = self.cache.get(key, max_bytes=32 * 1024 * 1024)
        if cached is not None:
            return cached
        source, _media_type = self.provider().original(photo_id)
        self.cache.put(key, source)
        return source

    def cache_stats(self) -> CacheStats:
        self.cache.set_max_bytes(self.repository.load().refresh.cache_max_bytes)
        return self.cache.stats()

    def reset_to_defaults(self) -> list[str]:
        """Clear app data and runtime state, returning any cleanup failures."""
        if self.renderer.hardware_busy:
            raise RuntimeError("Wait for the current frame update to finish before resetting")
        failures: list[str] = []
        with self._claim_lock, self._runtime_lock:
            for label, clear in (
                ("saved credential", self.secrets.clear),
                ("downloaded photo cache", self.cache.clear),
            ):
                try:
                    clear()
                except OSError:
                    failures.append(label)
            # SettingsRepository.save performs one validated atomic replacement.
            self.repository.save(AppSettings())
            self.albums = []
            self.photos = []
            self.selected_preview_id = None
            self._preserved_display_photo = None
            self._prepared_image = None
            self._loaded = False
            self._startup_catalog_restore_pending = True
            self.display = None
            self.renderer.reset()
        return failures

    def refresh_lifecycle(self, now: datetime | None = None) -> bool:
        try:
            with self.maintenance_gate.operation():
                return self._refresh_lifecycle(now)
        except MaintenanceError:
            return False

    def _refresh_lifecycle(self, now: datetime | None = None) -> bool:
        with self._claim_lock:
            if self._lifecycle_inflight or self._album_change_inflight:
                return False
            self._lifecycle_inflight = True
            self._refresh_idle.clear()
        try:
            settings = self.repository.load()
            _albums, photos = self.catalog_snapshot()
            if settings.frame.album_id and not photos:
                current = now or datetime.now(UTC)
                # Bypass a persisted success schedule exactly once after process
                # startup. A failed restoration then obeys its retry deadline.
                startup_bypass = (
                    self._startup_catalog_restore_pending
                    and settings.refresh_status.consecutive_failures == 0
                    and settings.refresh_status.last_error is None
                )
                self._startup_catalog_restore_pending = False
                restore_due = startup_bypass or RefreshCoordinator.due(settings, current)
                if restore_due:
                    try:
                        self.refresh_photos()
                    except (ProviderError, RuntimeError, OSError) as exc:
                        message = str(exc)[:500]

                        def record_restart_failure(saved):
                            status = saved.refresh_status
                            status.last_attempt_at = current
                            status.consecutive_failures += 1
                            status.last_error = message
                            status.next_attempt_at = current + timedelta(
                                seconds=saved.refresh.retry_seconds
                            )

                        self.repository.update(record_restart_failure)
                        return True
                    settings = self.repository.load()
            attempted = self.refresh_coordinator.run_if_due(settings, now)
            if attempted:
                refreshed_status = settings.refresh_status.model_copy(deep=True)
                self.repository.update(
                    lambda saved: setattr(saved, "refresh_status", refreshed_status)
                )
            self._advance_scheduled_render(now)
            return attempted
        except AlbumLoadCancelled:
            # Do not immediately restart a cancelled cold load on the next worker
            # tick. A new album selection remains free to start immediately.
            self.repository.update(
                lambda saved: setattr(
                    saved.refresh_status,
                    "next_attempt_at",
                    (now or datetime.now(UTC))
                    + timedelta(seconds=saved.refresh.catalog_refresh_seconds),
                )
            )
            return False
        finally:
            with self._claim_lock:
                self._lifecycle_inflight = False
                self._refresh_idle.set()

    def record_worker_failure(self, exc: Exception) -> None:
        current = datetime.now(UTC)
        message = f"Refresh worker error: {exc}"[:500]

        def record(settings):
            status = settings.refresh_status
            status.last_attempt_at = current
            status.consecutive_failures += 1
            status.last_error = message
            status.next_attempt_at = current + timedelta(seconds=settings.refresh.retry_seconds)

        self.repository.update(record)

    def _advance_scheduled_render(self, now: datetime | None = None) -> None:
        """Render one recent due occurrence, surviving restarts without replay storms."""
        if self.maintenance_gate.maintenance or self._album_change_inflight:
            return
        current = now or datetime.now(UTC)
        settings = self.repository.load()
        self.renderer.update(settings.device, current)
        settings = self.repository.load()
        _albums, photos = self.catalog_snapshot()
        if self.renderer.snapshot().active or self.renderer.hardware_busy or not photos:
            return
        occurrence = catch_up_occurrence(settings.frame, settings.device.timezone, current)
        if (
            occurrence is None
            or settings.refresh_status.last_attempted_schedule_key == occurrence.key
        ):
            return
        eligible = self.photo_eligibility(settings.frame, photos).eligible
        candidate = next_photo(
            eligible,
            settings.frame,
            settings.refresh_status.last_rendered_photo_id,
        )
        if candidate is None:
            selection = active_selection(eligible, settings.frame, current)
            candidate = selection.photo
        if not candidate:
            return
        try:
            # Autonomous output is a physical-frame responsibility. Demo and
            # off-device development retain their existing manual simulation.
            if not self.has_display():
                return
            anchor = settings.frame.schedule_anchor

            # Claim the occurrence durably before touching the provider or
            # display. A failure or restart must never replay the same slot.
            self.repository.update(
                lambda saved: setattr(
                    saved.refresh_status, "last_attempted_schedule_key", occurrence.key
                )
            )

            def completed(photo_id: str) -> None:
                def save_completion(saved):
                    saved.refresh_status.last_completed_schedule_anchor = anchor
                    saved.refresh_status.last_completed_schedule_slot = (
                        int(occurrence.key.rsplit(":", 1)[-1])
                        if occurrence.key.startswith("interval:")
                        else None
                    )
                    saved.refresh_status.last_completed_schedule_key = occurrence.key
                    saved.refresh_status.last_rendered_photo_id = photo_id
                    saved.refresh_status.last_render_error = None

                self.repository.update(save_completion)

            def failed(message: str) -> None:
                self.repository.update(
                    lambda saved: setattr(saved.refresh_status, "last_render_error", message[:500])
                )

            self.render_service.start(candidate.id, on_complete=completed, on_failure=failed)
        except (ImageProcessingError, ProviderError, RuntimeError) as exc:
            message = str(exc)[:500]
            self.repository.update(
                lambda saved: setattr(saved.refresh_status, "last_render_error", message)
            )
