import os
import secrets as secure_random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, RLock
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image

from . import __version__
from .cache import CacheStats, PhotoCache
from .display import InkyDisplay, apply_profile, discover_inky
from .image_processing import ImageProcessingError, image_is_decodable, prepare_for_display
from .lifecycle import RefreshCoordinator, RefreshWorker, health_payload
from .models import (
    Album,
    AppSettings,
    CertificateMode,
    DisplayDriver,
    FrameSettings,
    NetworkAccess,
    NetworkSettings,
    Orientation,
    Photo,
    PhotoOrder,
    ProviderKind,
    WebProtocol,
)
from .providers import DemoProvider, ImmichProvider, PhotoProvider, ProviderError
from .renderer import MockRenderCoordinator, RenderPhase, RenderService
from .selector import EligibilitySummary, active_selection, classify_photos, shuffled_photo_ids
from .settings import SecretStore, SettingsRepository
from .tls import tls_paths

ProviderFactory = Callable[[str, str], PhotoProvider]


def schedule_label(target: datetime | None, timezone: str, now: datetime | None = None) -> str:
    if not target:
        return "Not scheduled"
    zone = ZoneInfo(timezone)
    current = (now or datetime.now(UTC)).astimezone(zone)
    local_target = target.astimezone(zone)
    seconds = max(0, int((local_target - current).total_seconds()))
    if seconds < 60:
        relative = "in <1 min"
    elif seconds < 3600:
        relative = f"in {seconds // 60} min"
    elif seconds < 7200:
        relative = "in 1 hr"
    else:
        relative = f"in {seconds // 3600} hrs"
    prefix = local_target.strftime("%H:%M")
    if local_target.date() != current.date():
        date_format = "%a %#d %b · %H:%M" if os.name == "nt" else "%a %-d %b · %H:%M"
        prefix = local_target.strftime(date_format)
    return f"{prefix} · {relative}"


def rotation_interval_label(seconds: int) -> str:
    """Return a compact, human-readable saved rotation interval."""
    for unit_seconds, unit_name in ((86_400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            amount = seconds // unit_seconds
            return f"{amount} {unit_name}{'' if amount == 1 else 's'}"
    return f"{seconds} second{'' if seconds == 1 else 's'}"


class Runtime:
    def __init__(
        self,
        settings: SettingsRepository,
        secrets: SecretStore,
        factory: ProviderFactory,
        demo_provider: PhotoProvider | None = None,
    ):
        self.repository, self.secrets, self.factory = settings, secrets, factory
        self.demo_provider = demo_provider
        self.albums: list[Album] = []
        self.photos: list[Photo] = []
        self._loaded = False
        self.selected_preview_id: str | None = None
        self._prepared_image: Image.Image | None = None
        self.renderer = MockRenderCoordinator()
        self.display: InkyDisplay | None = None
        self.cache = PhotoCache(settings.data_dir, self.repository.load().refresh.cache_max_bytes)
        self.refresh_coordinator = RefreshCoordinator(self)
        self._runtime_lock = RLock()
        self._eligibility_lock = RLock()
        self._claim_lock = Lock()
        self._lifecycle_inflight = False
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
        settings = self.repository.load()
        key = self.secrets.get_api_key()
        if not settings.provider.server_url or not key:
            raise ProviderError("Save an Immich server URL and API key first")
        return self.factory(str(settings.provider.server_url), key)

    def refresh_albums(self) -> list[Album]:
        albums = self.provider().list_albums()
        with self._runtime_lock:
            self.albums = albums
            self.loaded = True
            return list(self.albums)

    def refresh_photos(self) -> list[Photo]:
        album_id = self.repository.load().frame.album_id
        photos = self.provider().list_photos(album_id) if album_id else []
        self.photo_eligibility(self.repository.load().frame, photos)
        with self._runtime_lock:
            self.photos = photos
        self.reconcile_shuffle()
        return list(photos)

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

    def preview_id(self) -> str | None:
        with self._runtime_lock:
            return self.selected_preview_id

    def set_preview(self, photo_id: str | None) -> None:
        with self._runtime_lock:
            self.selected_preview_id = photo_id

    def _cache_key(self, photo_id: str) -> str:
        return f"{self.repository.load().provider.kind}:{photo_id}"

    def photo_is_decodable(self, photo_id: str) -> bool:
        """Verify an asset once, persisting the installed-pipeline verdict."""
        key = self._cache_key(photo_id)
        with self._eligibility_lock:
            known = self.cache.decodability(key)
            if known is not None:
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
        self, frame: FrameSettings, photos: list[Photo] | None = None
    ) -> EligibilitySummary:
        candidates = photos if photos is not None else self.catalog_snapshot()[1]
        return classify_photos(
            candidates,
            frame,
            lambda photo: self.photo_is_decodable(photo.id),
        )

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
        cached = self.cache.get(key)
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
            self._prepared_image = None
            self._loaded = False
            self._startup_catalog_restore_pending = True
            self.display = None
            self.renderer.reset()
        return failures

    def refresh_lifecycle(self, now: datetime | None = None) -> bool:
        with self._claim_lock:
            if self._lifecycle_inflight:
                return False
            self._lifecycle_inflight = True
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
        finally:
            with self._claim_lock:
                self._lifecycle_inflight = False

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
        """Render each current schedule slot once, surviving process restarts."""
        current = now or datetime.now(UTC)
        settings = self.repository.load()
        self.renderer.update(settings.device, current)
        settings = self.repository.load()
        _albums, photos = self.catalog_snapshot()
        if self.renderer.snapshot().active or self.renderer.hardware_busy or not photos:
            return
        elapsed = int((current - settings.frame.schedule_anchor).total_seconds())
        slot = max(0, elapsed // settings.frame.rotation_seconds)
        if (
            settings.refresh_status.last_completed_schedule_anchor == settings.frame.schedule_anchor
            and settings.refresh_status.last_completed_schedule_slot == slot
        ):
            return
        eligible = self.photo_eligibility(settings.frame, photos).eligible
        selection = active_selection(eligible, settings.frame, current)
        if not selection.photo:
            return
        try:
            # Autonomous output is a physical-frame responsibility. Demo and
            # off-device development retain their existing manual simulation.
            if not self.has_display():
                return
            anchor = settings.frame.schedule_anchor

            def completed(photo_id: str) -> None:
                def save_completion(saved):
                    saved.refresh_status.last_completed_schedule_anchor = anchor
                    saved.refresh_status.last_completed_schedule_slot = slot
                    saved.refresh_status.last_rendered_photo_id = photo_id
                    saved.refresh_status.last_render_error = None

                self.repository.update(save_completion)

            def failed(message: str) -> None:
                self.repository.update(
                    lambda saved: setattr(saved.refresh_status, "last_render_error", message[:500])
                )

            self.render_service.start(selection.photo.id, on_complete=completed, on_failure=failed)
        except (ImageProcessingError, ProviderError, RuntimeError) as exc:
            message = str(exc)[:500]
            self.repository.update(
                lambda saved: setattr(saved.refresh_status, "last_render_error", message)
            )


def create_app(
    data_dir: Path | None = None,
    provider_factory: ProviderFactory | None = None,
    demo_mode: bool | None = None,
    restart_callback: Callable[[], None] | None = None,
) -> FastAPI:
    package = Path(__file__).parent
    target = data_dir or Path(os.getenv("PHOTOFRAME_DATA_DIR", "data"))
    repository, secrets = SettingsRepository(target), SecretStore(target)
    factory = provider_factory or (lambda url, key: ImmichProvider(url, key))
    is_demo = demo_mode if demo_mode is not None else os.getenv("PHOTOFRAME_DEMO_MODE") == "1"
    runtime = Runtime(repository, secrets, factory, DemoProvider() if is_demo else None)
    reset_lock = Lock()
    if not is_demo:
        runtime.initialise_display()
    templates = Jinja2Templates(directory=package / "templates")

    if is_demo:

        def configure_demo(settings):
            settings.verification.ok = True
            settings.verification.message = "Local demo library ready"
            settings.provider.server_url = "http://demo.local"
            settings.frame.album_id = "demo-album"
            settings.frame.album_name = "Quiet places"
            settings.device.expected_refresh_seconds = int(
                os.getenv("PHOTOFRAME_DEMO_REFRESH_SECONDS", "8")
            )
            # This only configures the mock preview. Production installations
            # must set their actual panel dimensions in workflow settings.
            settings.device.set_display_size(1200, 750)

        repository.update(configure_demo)
        runtime.refresh_albums()
        runtime.refresh_photos()
        runtime.refresh_lifecycle()

    app = FastAPI(title="Photoframe", version=__version__)
    app.mount("/static", StaticFiles(directory=package / "static"), name="static")
    app.state.runtime = runtime
    worker = RefreshWorker(runtime.refresh_lifecycle, runtime.record_worker_failure)

    @app.on_event("startup")
    def start_refresh_worker() -> None:
        worker.start()

    @app.on_event("shutdown")
    def stop_refresh_worker() -> None:
        worker.stop()

    def workspace_context(
        request: Request, notice: str | None = None, error: str | None = None
    ) -> dict:
        current = datetime.now(UTC)
        settings = repository.load()
        if not runtime.loaded and settings.verification.ok:
            try:
                runtime.refresh_albums()
                if settings.frame.album_id:
                    runtime.refresh_photos()
            except (ProviderError, RuntimeError) as exc:
                error = str(exc)
                runtime.loaded = True
        albums, photos = runtime.catalog_snapshot()
        eligibility = runtime.photo_eligibility(settings.frame, photos)
        eligible = eligibility.eligible
        selection = active_selection(eligible, settings.frame, current)
        render_state = runtime.renderer.update(settings.device, current)
        selected_photo = runtime.photo(runtime.preview_id())
        rendered_photo = runtime.photo(runtime.renderer.rendered_photo_id())
        displayed_photo = rendered_photo or selection.photo
        render_photo = runtime.photo(render_state.photo_id)
        seconds_until_change = (
            max(0, int((selection.next_change_at - current).total_seconds()))
            if selection.next_change_at
            else None
        )
        next_selection = (
            active_selection(eligible, settings.frame, selection.next_change_at)
            if selection.next_change_at
            else None
        )
        next_photo = next_selection.photo if next_selection else None
        scheduled_transition_soon = bool(
            seconds_until_change is not None
            and seconds_until_change <= 60
            and next_photo
            and (not displayed_photo or next_photo.id != displayed_photo.id)
        )
        phases = [
            (RenderPhase.PREPARING, "Preparing image"),
            (RenderPhase.SENDING, "Sending to frame"),
            (RenderPhase.WAITING, "Waiting for e-ink refresh"),
            (RenderPhase.COMPLETE, "Complete"),
        ]
        return {
            "request": request,
            "settings": settings,
            "credential_saved": secrets.exists(),
            "albums": albums,
            "photos": photos,
            "eligible": eligible,
            "wrong_orientation_count": eligibility.wrong_orientation,
            "unsupported_count": eligibility.unsupported,
            "selection": selection,
            "displayed_photo": displayed_photo,
            "selected_photo": selected_photo,
            "render_photo": render_photo,
            "next_photo": next_photo,
            "seconds_until_change": seconds_until_change,
            "scheduled_transition_soon": scheduled_transition_soon,
            "render_state": render_state,
            "render_phases": phases,
            "RenderPhase": RenderPhase,
            "next_change": schedule_label(
                selection.next_change_at, settings.device.timezone, current
            ),
            "source_name": "Demo library" if is_demo else settings.provider.kind.value.title(),
            "source_detail": (
                "Local preview · no network"
                if is_demo
                else str(settings.provider.server_url or "Not configured")
            ),
            "notice": notice,
            "error": error,
            "demo_mode": is_demo,
            "schedule_order": (
                "Scheduled shuffle"
                if settings.frame.photo_order == PhotoOrder.SHUFFLE
                else "Album order"
            ),
            "network_summary": (
                f"{'This device only' if settings.network.access == NetworkAccess.DEVICE_ONLY else 'Local network'}"
                f" · {settings.network.protocol.value.upper()} · {settings.network.port}"
            ),
            "network_address": settings.network.display_address,
            "display_summary": (
                f"Every {rotation_interval_label(settings.frame.rotation_seconds)} · "
                f"{'Shuffle' if settings.frame.photo_order == PhotoOrder.SHUFFLE else 'In album order'}"
                f" · {settings.frame.orientation.value.title()}"
            ),
        }

    def workspace(
        request: Request, notice: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "_workspace.html",
            workspace_context(request, notice=notice, error=error),
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {})

    @app.get("/partials/workspace", response_class=HTMLResponse)
    def workspace_partial(request: Request) -> HTMLResponse:
        return workspace(request)

    @app.get("/partials/frame-status", response_class=HTMLResponse)
    def frame_status_partial(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_frame_status.html", workspace_context(request))

    @app.get("/partials/render-status", response_class=HTMLResponse)
    def render_status_partial(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "_render_status.html", workspace_context(request)
        )

    @app.post("/connection", response_class=HTMLResponse)
    async def save_connection(request: Request) -> HTMLResponse:
        form = await request.form()
        server_url = str(form.get("server_url", "")).strip()
        api_key = str(form.get("api_key", "")).strip()
        try:

            def connection_change(settings):
                settings.provider.kind = ProviderKind.IMMICH
                settings.provider.server_url = server_url

            repository.update(connection_change)
            if api_key:
                secrets.set_api_key(api_key)
            message = runtime.provider().validate_connection()
            checked_at = datetime.now(UTC)

            def record_connection_success(settings: AppSettings) -> None:
                settings.verification.ok = True
                settings.verification.message = message
                settings.verification.last_checked_at = checked_at

            repository.update(record_connection_success)
            runtime.refresh_albums()
            with runtime._runtime_lock:
                runtime.photos = []
            return workspace(request, notice=message)
        except Exception as exc:
            message = str(exc)
            checked_at = datetime.now(UTC)

            def record_connection_failure(settings: AppSettings) -> None:
                settings.verification.ok = False
                settings.verification.message = message
                settings.verification.last_checked_at = checked_at

            repository.update(record_connection_failure)
            return workspace(request, error=str(exc))

    @app.post("/albums/refresh", response_class=HTMLResponse)
    def refresh_albums(request: Request) -> HTMLResponse:
        try:
            albums = runtime.refresh_albums()
            return workspace(request, notice=f"Loaded {len(albums)} albums")
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/album/select", response_class=HTMLResponse)
    async def select_album(request: Request) -> HTMLResponse:
        album_id = str((await request.form()).get("album_id", ""))
        albums, _photos = runtime.catalog_snapshot()
        album = next((item for item in albums if item.id == album_id), None)
        if not album:
            return workspace(request, error="Choose an album from the loaded list")
        anchor = datetime.now(UTC)

        def choose_album(settings):
            settings.frame.album_id, settings.frame.album_name = album.id, album.name
            settings.frame.schedule_anchor = anchor
            settings.frame.starting_photo_id = None
            settings.refresh_status.next_attempt_at = None

        repository.update(choose_album)
        runtime.set_preview(None)
        try:
            photos = runtime.refresh_photos()
            runtime.refresh_lifecycle()
            return workspace(request, notice=f"Selected {album.name}; found {len(photos)} images")
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/workflow", response_class=HTMLResponse)
    async def save_workflow(request: Request) -> HTMLResponse:
        form = await request.form()
        try:
            width = str(form.get("display_width_px", "")).strip()
            height = str(form.get("display_height_px", "")).strip()
            if bool(width) != bool(height):
                raise ValueError("Enter both native display dimensions, or leave both blank")
            _albums, photos = runtime.catalog_snapshot()
            anchor = datetime.now(UTC)

            def workflow_change(settings):
                previous_order = settings.frame.photo_order
                settings.frame.orientation = Orientation(str(form.get("orientation")))
                settings.frame.rotation_seconds = int(str(form.get("rotation_seconds")))
                settings.frame.photo_order = PhotoOrder(
                    str(form.get("photo_order", settings.frame.photo_order.value))
                )
                settings.device.timezone = str(form.get("timezone") or settings.device.timezone)
                settings.device.expected_refresh_seconds = int(
                    str(form.get("expected_refresh_seconds"))
                )
                settings.device.render_timeout_seconds = int(
                    str(form.get("render_timeout_seconds"))
                )
                settings.device.display_driver = DisplayDriver(
                    str(form.get("display_driver", "auto"))
                )
                settings.device.display_model = str(form.get("display_model") or "").strip() or None
                settings.device.set_display_size(
                    int(width) if width else None, int(height) if height else None
                )
                settings.frame.schedule_anchor = anchor
                if not any(
                    p.id == settings.frame.starting_photo_id
                    and p.matches(settings.frame.orientation)
                    for p in photos
                ):
                    settings.frame.starting_photo_id = None
                if settings.frame.photo_order == PhotoOrder.ALBUM:
                    settings.frame.shuffle_photo_ids = []
                    settings.frame.shuffle_seed = 0
                elif previous_order != PhotoOrder.SHUFFLE:
                    settings.frame.shuffle_seed = secure_random.randbits(63)
                    settings.frame.shuffle_photo_ids = []

            repository.update(workflow_change)
            saved_frame = repository.load().frame
            eligible_ids = {
                photo.id for photo in runtime.photo_eligibility(saved_frame, photos).eligible
            }
            if saved_frame.starting_photo_id not in eligible_ids:
                repository.update(lambda saved: setattr(saved.frame, "starting_photo_id", None))
            if runtime.preview_id() not in eligible_ids:
                runtime.set_preview(None)
            runtime.reconcile_shuffle()
            runtime.initialise_display()
            return workspace(request, notice="Frame settings saved; rotation restarted from now")
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/network", response_class=HTMLResponse)
    async def save_network(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        form = await request.form()
        try:
            raw_port = str(form.get("network_port", "")).strip()
            try:
                port = int(raw_port)
            except ValueError as exc:
                raise ValueError("Listening port must be a whole number from 1 to 65535") from exc
            if not 1 <= port <= 65535:
                raise ValueError("Listening port must be between 1 and 65535")
            candidate = NetworkSettings(
                access=NetworkAccess(str(form.get("network_access", "device_only"))),
                port=port,
                protocol=WebProtocol(str(form.get("web_protocol", "http"))),
                certificate_mode=CertificateMode(str(form.get("certificate_mode", "automatic"))),
                certificate_path=str(form.get("certificate_path", "")).strip() or None,
                private_key_path=str(form.get("private_key_path", "")).strip() or None,
            )
            current = repository.load().network
            listener_changed = candidate != current
            if listener_changed and str(form.get("confirm_endpoint_change", "")) != "yes":
                raise ValueError(
                    "Confirm the listener change before saving; Photoframe must restart and the "
                    f"new address will be {candidate.display_address}"
                )
            # Generate or validate TLS material before committing a configuration
            # which Uvicorn could not start.
            tls_paths(target, candidate)
            repository.update(lambda settings: setattr(settings, "network", candidate))
            if listener_changed:
                message = (
                    f"Network settings saved. Photoframe is restarting; open "
                    f"{candidate.display_address} when it is ready."
                )
                if restart_callback:
                    background_tasks.add_task(restart_callback)
                else:
                    message = (
                        f"Network settings saved. Restart Photoframe, then open "
                        f"{candidate.display_address}."
                    )
            else:
                message = "Network settings are already up to date"
            return workspace(request, notice=message)
        except (OSError, ValueError) as exc:
            return workspace(request, error=f"Network settings were not saved: {exc}")

    @app.post("/photo/preview", response_class=HTMLResponse)
    async def preview_photo(request: Request) -> HTMLResponse:
        photo_id = str((await request.form()).get("photo_id", ""))
        settings = repository.load()
        _albums, photos = runtime.catalog_snapshot()
        eligible_ids = {
            photo.id for photo in runtime.photo_eligibility(settings.frame, photos).eligible
        }
        if photo_id not in eligible_ids:
            return workspace(
                request,
                error="That image is not eligible or cannot be decoded by this PhotoFrame",
            )
        runtime.set_preview(photo_id)
        runtime.renderer.reset()
        return workspace(request)

    @app.post("/photo/preview/clear", response_class=HTMLResponse)
    def clear_preview(request: Request) -> HTMLResponse:
        runtime.set_preview(None)
        runtime.renderer.reset()
        return workspace(request)

    @app.post("/photo/start", response_class=HTMLResponse)
    async def start_photo(request: Request) -> HTMLResponse:
        photo_id = str((await request.form()).get("photo_id", ""))
        settings = repository.load()
        _albums, photos = runtime.catalog_snapshot()
        eligible_ids = {
            photo.id for photo in runtime.photo_eligibility(settings.frame, photos).eligible
        }
        if photo_id not in eligible_ids:
            return workspace(
                request,
                error="That image is not eligible or cannot be decoded by this PhotoFrame",
            )
        anchor = datetime.now(UTC)

        def start_from_photo(saved: AppSettings) -> None:
            saved.frame.starting_photo_id = photo_id
            saved.frame.schedule_anchor = anchor

        repository.update(start_from_photo)
        if settings.frame.photo_order == PhotoOrder.SHUFFLE:
            runtime.reconcile_shuffle(anchor_id=photo_id, fresh=True)
            message = "A new shuffled round now starts from the selected image"
        else:
            message = "Rotation now starts from the selected image"
        return workspace(request, notice=message)

    @app.post("/reset", response_class=HTMLResponse)
    def reset_photoframe(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        if not reset_lock.acquire(blocking=False):
            return workspace(request, error="Photoframe reset is already in progress")
        try:
            listener_changed = repository.load().network != NetworkSettings()
            failures = runtime.reset_to_defaults()
            if failures:
                return workspace(
                    request,
                    error=(
                        "Reset incomplete. Your configuration was cleared, but some local photo "
                        "data could not be removed. Retry cleanup before reconnecting."
                    ),
                )
            response = workspace(
                request,
                notice=(
                    "Photoframe was reset to defaults and is restarting at http://127.0.0.1:8000."
                    if listener_changed and restart_callback
                    else "Photoframe was reset to defaults. Connect a photo provider to begin."
                ),
            )
            if listener_changed and restart_callback:
                background_tasks.add_task(restart_callback)
            return response
        except (OSError, RuntimeError, ValueError) as exc:
            return workspace(request, error=f"Photoframe could not be reset: {exc}")
        finally:
            reset_lock.release()

    @app.post("/render/start", response_class=HTMLResponse)
    def render_start(request: Request) -> HTMLResponse:
        photo = runtime.photo(runtime.preview_id())
        if not photo:
            return workspace(
                request, error="Select an image preview before sending it to the frame"
            )
        try:
            if runtime.has_display():
                runtime.render_service.start(photo.id)
            else:
                runtime.prepare_photo(photo.id)
                runtime.renderer.start(photo.id)
        except (ImageProcessingError, ProviderError, RuntimeError) as exc:
            if isinstance(exc, ImageProcessingError):
                runtime.set_preview(None)
            return workspace(request, error=str(exc))
        return workspace(request)

    @app.post("/render/reset", response_class=HTMLResponse)
    def render_reset(request: Request) -> HTMLResponse:
        runtime.renderer.reset()
        runtime.set_preview(None)
        return workspace(request)

    @app.post("/render/dismiss", response_class=HTMLResponse)
    def render_dismiss() -> HTMLResponse:
        if runtime.renderer.snapshot().phase == RenderPhase.COMPLETE:
            runtime.renderer.reset()
            runtime.set_preview(None)
        return HTMLResponse("")

    @app.get("/thumbnail/{photo_id}")
    def thumbnail(photo_id: str) -> Response:
        try:
            content, media_type = runtime.provider().thumbnail(photo_id)
            return Response(
                content, media_type=media_type, headers={"Cache-Control": "private, max-age=300"}
            )
        except ProviderError as exc:
            return Response(str(exc), status_code=502, media_type="text/plain")

    @app.get("/health")
    def health() -> JSONResponse:
        # Uptime Kuma can use the HTTP code, while the JSON body gives an
        # operator the retry, cache, and stale-health context.
        settings = repository.load()
        body, healthy = health_payload(settings)
        return JSONResponse(jsonable_encoder(body), status_code=200 if healthy else 503)

    return app


app = create_app()
