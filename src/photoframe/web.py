import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image

from .cache import CacheStats, PhotoCache
from .display import InkyDisplay, apply_profile, discover_inky
from .image_processing import ImageProcessingError, prepare_for_display
from .lifecycle import RefreshCoordinator, RefreshWorker, health_payload
from .models import Album, DisplayDriver, Orientation, Photo, ProviderKind
from .providers import DemoProvider, ImmichProvider, PhotoProvider, ProviderError
from .renderer import MockRenderCoordinator, RenderPhase
from .selector import active_selection
from .settings import SecretStore, SettingsRepository

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
        self.loaded = False
        self.selected_preview_id: str | None = None
        self.prepared_image: Image.Image | None = None
        self.renderer = MockRenderCoordinator()
        self.display: InkyDisplay | None = None
        self.cache = PhotoCache(settings.data_dir, self.repository.load().refresh.cache_max_bytes)
        self.refresh_coordinator = RefreshCoordinator(self)

    def initialise_display(self) -> None:
        """Probe Inky once at boot and retain its reported native capabilities."""
        settings = self.repository.load()
        display, profile = discover_inky(settings.device)
        self.display = display
        settings.device.display_status = profile.status
        if display or settings.device.display_size:
            apply_profile(settings.device, profile)
        self.repository.save(settings)

    def provider(self) -> PhotoProvider:
        if self.demo_provider:
            return self.demo_provider
        settings = self.repository.load()
        key = self.secrets.get_api_key()
        if not settings.provider.server_url or not key:
            raise ProviderError("Save an Immich server URL and API key first")
        return self.factory(str(settings.provider.server_url), key)

    def refresh_albums(self) -> list[Album]:
        self.albums = self.provider().list_albums()
        self.loaded = True
        return self.albums

    def refresh_photos(self) -> list[Photo]:
        album_id = self.repository.load().frame.album_id
        self.photos = self.provider().list_photos(album_id) if album_id else []
        return self.photos

    def photo(self, photo_id: str | None) -> Photo | None:
        return next((photo for photo in self.photos if photo.id == photo_id), None)

    def prepare_photo(self, photo_id: str) -> Image.Image:
        target_size = self.repository.load().device.display_size
        if not target_size:
            raise ImageProcessingError(
                "Set the frame's native display width and height before rendering"
            )
        source = self.cache_photo(photo_id)
        self.prepared_image = prepare_for_display(source, target_size)
        return self.prepared_image

    def cache_photo(self, photo_id: str) -> bytes:
        """Get an original locally first, then acquire and safely cache it."""
        settings = self.repository.load()
        self.cache.max_bytes = settings.refresh.cache_max_bytes
        key = f"{settings.provider.kind}:{photo_id}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        source, _media_type = self.provider().original(photo_id)
        self.cache.put(key, source)
        return source

    def cache_stats(self) -> CacheStats:
        self.cache.max_bytes = self.repository.load().refresh.cache_max_bytes
        return self.cache.stats()

    def refresh_lifecycle(self) -> bool:
        settings = self.repository.load()
        attempted = self.refresh_coordinator.run_if_due(settings)
        if attempted:
            self.repository.save(settings)
        return attempted


def create_app(
    data_dir: Path | None = None,
    provider_factory: ProviderFactory | None = None,
    demo_mode: bool | None = None,
) -> FastAPI:
    package = Path(__file__).parent
    target = data_dir or Path(os.getenv("PHOTOFRAME_DATA_DIR", "data"))
    repository, secrets = SettingsRepository(target), SecretStore(target)
    factory = provider_factory or (lambda url, key: ImmichProvider(url, key))
    is_demo = demo_mode if demo_mode is not None else os.getenv("PHOTOFRAME_DEMO_MODE") == "1"
    runtime = Runtime(repository, secrets, factory, DemoProvider() if is_demo else None)
    if not is_demo:
        runtime.initialise_display()
    templates = Jinja2Templates(directory=package / "templates")

    if is_demo:
        demo_settings = repository.load()
        demo_settings.verification.ok = True
        demo_settings.verification.message = "Local demo library ready"
        demo_settings.provider.server_url = "http://demo.local"
        demo_settings.frame.album_id = "demo-album"
        demo_settings.frame.album_name = "Quiet places"
        demo_settings.device.expected_refresh_seconds = int(
            os.getenv("PHOTOFRAME_DEMO_REFRESH_SECONDS", "8")
        )
        # This only configures the mock preview. Production installations must
        # set their actual panel dimensions in the workflow settings.
        demo_settings.device.set_display_size(1200, 750)
        repository.save(demo_settings)
        runtime.albums = runtime.provider().list_albums()
        runtime.photos = runtime.provider().list_photos("demo-album")
        runtime.loaded = True
        runtime.refresh_lifecycle()

    app = FastAPI(title="Photoframe", version="1.0.0")
    app.mount("/static", StaticFiles(directory=package / "static"), name="static")
    app.state.runtime = runtime
    worker = RefreshWorker(runtime.refresh_lifecycle)

    @app.on_event("startup")
    def start_refresh_worker() -> None:
        worker.start()

    @app.on_event("shutdown")
    def stop_refresh_worker() -> None:
        worker.stop()

    def workspace(
        request: Request, notice: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        settings = repository.load()
        if not runtime.loaded and settings.verification.ok:
            try:
                runtime.refresh_albums()
                if settings.frame.album_id:
                    runtime.refresh_photos()
            except (ProviderError, RuntimeError) as exc:
                error = str(exc)
                runtime.loaded = True
        selection = active_selection(runtime.photos, settings.frame)
        eligible = [photo for photo in runtime.photos if photo.matches(settings.frame.orientation)]
        render_state = runtime.renderer.update(settings.device)
        selected_photo = runtime.photo(runtime.selected_preview_id)
        rendered_photo = runtime.photo(runtime.renderer.last_rendered_photo_id)
        displayed_photo = rendered_photo or selection.photo
        render_photo = runtime.photo(render_state.photo_id)
        phases = [
            (RenderPhase.PREPARING, "Preparing image"),
            (RenderPhase.SENDING, "Sending to frame"),
            (RenderPhase.WAITING, "Waiting for e-ink refresh"),
            (RenderPhase.COMPLETE, "Complete"),
        ]
        return templates.TemplateResponse(
            request,
            "_workspace.html",
            {
                "settings": settings,
                "credential_saved": secrets.exists(),
                "albums": runtime.albums,
                "photos": runtime.photos,
                "eligible": eligible,
                "selection": selection,
                "displayed_photo": displayed_photo,
                "selected_photo": selected_photo,
                "render_photo": render_photo,
                "render_state": render_state,
                "render_phases": phases,
                "RenderPhase": RenderPhase,
                "next_change": schedule_label(selection.next_change_at, settings.device.timezone),
                "source_name": "Demo library" if is_demo else settings.provider.kind.value.title(),
                "source_detail": (
                    "Local preview · no network"
                    if is_demo
                    else str(settings.provider.server_url or "Not configured")
                ),
                "notice": notice,
                "error": error,
                "demo_mode": is_demo,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {})

    @app.get("/partials/workspace", response_class=HTMLResponse)
    def workspace_partial(request: Request) -> HTMLResponse:
        return workspace(request)

    @app.post("/connection", response_class=HTMLResponse)
    async def save_connection(request: Request) -> HTMLResponse:
        form = await request.form()
        server_url = str(form.get("server_url", "")).strip()
        api_key = str(form.get("api_key", "")).strip()
        settings = repository.load()
        try:
            settings.provider.kind = ProviderKind.IMMICH
            settings.provider.server_url = server_url
            if api_key:
                secrets.set_api_key(api_key)
            repository.save(settings)
            message = runtime.provider().validate_connection()
            settings.verification.ok = True
            settings.verification.message = message
            settings.verification.last_checked_at = datetime.now(UTC)
            repository.save(settings)
            runtime.refresh_albums()
            runtime.photos = []
            return workspace(request, notice=message)
        except Exception as exc:
            settings.verification.ok = False
            settings.verification.message = str(exc)
            settings.verification.last_checked_at = datetime.now(UTC)
            repository.save(settings)
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
        album = next((item for item in runtime.albums if item.id == album_id), None)
        if not album:
            return workspace(request, error="Choose an album from the loaded list")
        settings = repository.load()
        settings.frame.album_id, settings.frame.album_name = album.id, album.name
        settings.frame.schedule_anchor = datetime.now(UTC)
        settings.frame.starting_photo_id = None
        settings.refresh_status.next_attempt_at = None
        repository.save(settings)
        runtime.selected_preview_id = None
        try:
            photos = runtime.refresh_photos()
            runtime.refresh_lifecycle()
            return workspace(request, notice=f"Selected {album.name}; found {len(photos)} images")
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/workflow", response_class=HTMLResponse)
    async def save_workflow(request: Request) -> HTMLResponse:
        form = await request.form()
        settings = repository.load()
        try:
            settings.frame.orientation = Orientation(str(form.get("orientation")))
            settings.frame.rotation_seconds = int(str(form.get("rotation_seconds")))
            settings.device.timezone = str(form.get("timezone") or settings.device.timezone)
            settings.device.expected_refresh_seconds = int(
                str(form.get("expected_refresh_seconds"))
            )
            settings.device.render_timeout_seconds = int(str(form.get("render_timeout_seconds")))
            width = str(form.get("display_width_px", "")).strip()
            height = str(form.get("display_height_px", "")).strip()
            if bool(width) != bool(height):
                raise ValueError("Enter both native display dimensions, or leave both blank")
            settings.device.display_driver = DisplayDriver(str(form.get("display_driver", "auto")))
            settings.device.display_model = str(form.get("display_model") or "").strip() or None
            settings.device.set_display_size(
                int(width) if width else None,
                int(height) if height else None,
            )
            settings.frame.schedule_anchor = datetime.now(UTC)
            if not any(
                p.id == settings.frame.starting_photo_id and p.matches(settings.frame.orientation)
                for p in runtime.photos
            ):
                settings.frame.starting_photo_id = None
            repository.save(settings)
            runtime.initialise_display()
            return workspace(request, notice="Frame settings saved; rotation restarted from now")
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/photo/preview", response_class=HTMLResponse)
    async def preview_photo(request: Request) -> HTMLResponse:
        photo_id = str((await request.form()).get("photo_id", ""))
        settings = repository.load()
        if not any(
            p.id == photo_id and p.matches(settings.frame.orientation) for p in runtime.photos
        ):
            return workspace(request, error="That image is not eligible for this orientation")
        runtime.selected_preview_id = photo_id
        runtime.renderer.reset()
        return workspace(request)

    @app.post("/photo/preview/clear", response_class=HTMLResponse)
    def clear_preview(request: Request) -> HTMLResponse:
        runtime.selected_preview_id = None
        runtime.renderer.reset()
        return workspace(request)

    @app.post("/photo/start", response_class=HTMLResponse)
    async def start_photo(request: Request) -> HTMLResponse:
        photo_id = str((await request.form()).get("photo_id", ""))
        settings = repository.load()
        if not any(
            p.id == photo_id and p.matches(settings.frame.orientation) for p in runtime.photos
        ):
            return workspace(request, error="That image is not eligible for this orientation")
        settings.frame.starting_photo_id = photo_id
        settings.frame.schedule_anchor = datetime.now(UTC)
        repository.save(settings)
        return workspace(request, notice="Rotation now starts from the selected image")

    @app.post("/render/start", response_class=HTMLResponse)
    def render_start(request: Request) -> HTMLResponse:
        photo = runtime.photo(runtime.selected_preview_id)
        if not photo:
            return workspace(
                request, error="Select an image preview before sending it to the frame"
            )
        try:
            runtime.prepare_photo(photo.id)
            if runtime.display and runtime.prepared_image:
                runtime.renderer.start_hardware(
                    photo.id, lambda: runtime.display.show(runtime.prepared_image)
                )
            else:
                runtime.renderer.start(photo.id)
        except (ImageProcessingError, ProviderError, RuntimeError) as exc:
            return workspace(request, error=str(exc))
        return workspace(request)

    @app.post("/render/reset", response_class=HTMLResponse)
    def render_reset(request: Request) -> HTMLResponse:
        runtime.renderer.reset()
        runtime.selected_preview_id = None
        return workspace(request)

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
