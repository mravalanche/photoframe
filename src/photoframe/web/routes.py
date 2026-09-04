import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..lifecycle import RefreshWorker, health_payload
from ..models import NetworkAccess, PhotoOrder
from ..providers import ConfiguredProviderResolver, DemoProvider, ProviderError, ProviderResolver
from ..renderer import RenderPhase
from ..selector import active_selection
from ..services.configuration import ConfigurationService, ResetIncompleteError
from ..services.runtime import Runtime
from ..settings import SecretStore, SettingsRepository
from .forms import AlbumForm, ConnectionForm, NetworkForm, PhotoForm, WorkflowForm


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


def create_app(
    data_dir: Path | None = None,
    provider_factory: ProviderResolver | None = None,
    demo_mode: bool | None = None,
    restart_callback: Callable[[], None] | None = None,
) -> FastAPI:
    package = Path(__file__).parent.parent
    target = data_dir or Path(os.getenv("PHOTOFRAME_DATA_DIR", "data"))
    repository, secrets = SettingsRepository(target), SecretStore(target)
    provider_resolver = provider_factory or ConfiguredProviderResolver(repository, secrets)
    is_demo = demo_mode if demo_mode is not None else os.getenv("PHOTOFRAME_DEMO_MODE") == "1"
    runtime = Runtime(repository, secrets, provider_resolver, DemoProvider() if is_demo else None)
    configuration = ConfigurationService(repository, secrets, runtime, target)
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
        render_state = configuration.current_render_state(current)
        selected_photo = runtime.photo(runtime.preview_id())
        rendered_photo = runtime.photo(runtime.renderer.rendered_photo_id())
        displayed_photo = rendered_photo or selection.photo
        render_photo = runtime.photo(render_state.photo_id)
        completion_age_seconds = (
            max(0.0, (current - render_state.finished_at).total_seconds())
            if render_state.phase == RenderPhase.COMPLETE and render_state.finished_at
            else None
        )
        completion_visible_seconds = configuration.COMPLETED_RENDER_ACKNOWLEDGEMENT_SECONDS
        render_completion_visible = bool(
            completion_age_seconds is not None
            and completion_age_seconds < completion_visible_seconds
        )
        render_ack_delay_ms = (
            max(1, int((completion_visible_seconds - completion_age_seconds) * 1000))
            if render_completion_visible and completion_age_seconds is not None
            else 0
        )
        request_operation_id = request.headers.get("x-photoframe-render-intent")
        render_intent_matches = bool(
            render_state.operation_id and request_operation_id == render_state.operation_id
        )
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
            "render_completion_visible": render_completion_visible,
            "render_ack_delay_ms": render_ack_delay_ms,
            "render_intent_matches": render_intent_matches,
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
        try:
            message = configuration.save_connection(ConnectionForm.parse(await request.form()))
            return workspace(request, notice=message)
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/albums/refresh", response_class=HTMLResponse)
    def refresh_albums(request: Request) -> HTMLResponse:
        try:
            return workspace(request, notice=configuration.refresh_albums())
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/album/select", response_class=HTMLResponse)
    async def select_album(request: Request) -> HTMLResponse:
        try:
            form = AlbumForm.parse(await request.form())
            return workspace(request, notice=configuration.select_album(form.album_id))
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/workflow", response_class=HTMLResponse)
    async def save_workflow(request: Request) -> HTMLResponse:
        try:
            form = WorkflowForm.parse(await request.form())
            return workspace(request, notice=configuration.save_workflow(form))
        except Exception as exc:
            return workspace(request, error=str(exc))

    @app.post("/network", response_class=HTMLResponse)
    async def save_network(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        try:
            result = configuration.save_network(
                NetworkForm.parse(await request.form()), can_restart=restart_callback is not None
            )
            if result.restart_required and restart_callback:
                background_tasks.add_task(restart_callback)
            return workspace(request, notice=result.message)
        except (OSError, ValueError) as exc:
            return workspace(request, error=f"Network settings were not saved: {exc}")

    @app.post("/photo/preview", response_class=HTMLResponse)
    async def preview_photo(request: Request) -> HTMLResponse:
        try:
            form = PhotoForm.parse(await request.form())
            configuration.preview_photo(form.photo_id)
            return workspace(request)
        except ValueError as exc:
            return workspace(request, error=str(exc))

    @app.post("/photo/preview/clear", response_class=HTMLResponse)
    def clear_preview(request: Request) -> HTMLResponse:
        configuration.clear_preview()
        return workspace(request)

    @app.post("/photo/start", response_class=HTMLResponse)
    async def start_photo(request: Request) -> HTMLResponse:
        try:
            form = PhotoForm.parse(await request.form())
            return workspace(request, notice=configuration.start_from_photo(form.photo_id))
        except ValueError as exc:
            return workspace(request, error=str(exc))

    @app.post("/reset", response_class=HTMLResponse)
    def reset_photoframe(request: Request, background_tasks: BackgroundTasks) -> HTMLResponse:
        try:
            result = configuration.reset(can_restart=restart_callback is not None)
            response = workspace(request, notice=result.message)
            if result.restart_required and restart_callback:
                background_tasks.add_task(restart_callback)
            return response
        except ResetIncompleteError as exc:
            return workspace(request, error=str(exc))
        except ValueError as exc:
            if str(exc) == "Photoframe reset is already in progress":
                return workspace(request, error=str(exc))
            return workspace(request, error=f"Photoframe could not be reset: {exc}")
        except (OSError, RuntimeError) as exc:
            return workspace(request, error=f"Photoframe could not be reset: {exc}")

    @app.post("/render/start", response_class=HTMLResponse)
    def render_start(request: Request) -> HTMLResponse:
        operation_id = request.headers.get("x-photoframe-render-intent")
        try:
            operation_id = str(UUID(operation_id)) if operation_id else None
        except ValueError:
            operation_id = None
        try:
            configuration.start_render(operation_id=operation_id)
        except (ValueError, ProviderError, RuntimeError) as exc:
            return workspace(request, error=str(exc))
        return workspace(request)

    @app.post("/render/reset", response_class=HTMLResponse)
    def render_reset(request: Request) -> HTMLResponse:
        configuration.reset_render()
        return workspace(request)

    @app.post("/render/dismiss", response_class=HTMLResponse)
    def render_dismiss() -> HTMLResponse:
        configuration.dismiss_render()
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
