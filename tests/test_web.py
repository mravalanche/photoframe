from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from photoframe.models import Album, AppSettings, Photo, PhotoOrder
from photoframe.web import create_app

RENDER_INTENT = "38ebca82-c30a-4727-bb05-37fb455c97d6"
RENDER_HEADERS = {"X-Photoframe-Render-Intent": RENDER_INTENT}


class FakeProvider:
    def validate_connection(self):
        return "Connected for test"

    def list_albums(self):
        return [Album(id="album", name="Family", asset_count=2)]

    def list_photos(self, album_id):
        assert album_id == "album"
        return [
            Photo(id="wide", filename="wide.jpg", width=1200, height=800),
            Photo(id="tall", filename="tall.jpg", width=800, height=1200),
        ]

    def thumbnail(self, photo_id: str) -> tuple[bytes, str]:
        return b"image", "image/jpeg"

    def original(self, photo_id: str) -> tuple[bytes, str]:
        image = Image.new("RGB", (1200, 800), "navy")
        output = BytesIO()
        image.save(output, format="JPEG")
        return output.getvalue(), "image/jpeg"


def test_complete_local_web_flow(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        response = client.post(
            "/connection", data={"server_url": "https://immich.test", "api_key": "secret"}
        )
        assert response.status_code == 200
        assert "Connected for test" in response.text
        assert "secret" not in response.text
        response = client.post("/album/select", data={"album_id": "album"})
        assert "1 eligible" in response.text
        assert "1 wrong orientation" in response.text
        assert "0 unsupported or unreadable" in response.text
        assert "wide.jpg" in response.text
        response = client.post(
            "/workflow",
            data={
                "orientation": "portrait",
                "rotation_seconds": "300",
                "timezone": "Europe/London",
                "display_width_px": "800",
                "display_height_px": "480",
                "expected_refresh_seconds": "28",
                "render_timeout_seconds": "90",
            },
        )
        assert "tall.jpg" in response.text
        assert "Every 5 minutes · In album order · Portrait" in response.text
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"


class MixedFormatProvider(FakeProvider):
    def __init__(self):
        self.original_calls: list[str] = []

    def list_photos(self, album_id):
        assert album_id == "album"
        return [
            Photo(id="wide", filename="wide.jpg", width=1200, height=800),
            Photo(id="tall", filename="tall.jpg", width=800, height=1200),
            Photo(id="heic", filename="phone.heic", width=1200, height=800),
        ]

    def original(self, photo_id: str) -> tuple[bytes, str]:
        self.original_calls.append(photo_id)
        if photo_id == "heic":
            return b"HEIC-like bytes Pillow cannot decode", "image/heic"
        return super().original(photo_id)


class PreviewFallbackProvider(MixedFormatProvider):
    def __init__(self):
        super().__init__()
        self.thumbnail_calls: list[str] = []

    def thumbnail(self, photo_id: str) -> tuple[bytes, str]:
        self.thumbnail_calls.append(photo_id)
        if photo_id == "heic":
            return FakeProvider.original(self, photo_id)
        return super().thumbnail(photo_id)


def test_preview_does_not_change_schedule_until_explicit_actions(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        client.post("/connection", data={"server_url": "https://immich.test", "api_key": "secret"})
        client.post("/album/select", data={"album_id": "album"})
        client.post(
            "/workflow",
            data={
                "orientation": "landscape",
                "rotation_seconds": "300",
                "timezone": "Europe/London",
                "display_width_px": "1200",
                "display_height_px": "800",
                "expected_refresh_seconds": "28",
                "render_timeout_seconds": "90",
            },
        )
        runtime = app.state.runtime
        original_start = runtime.repository.load().frame.starting_photo_id
        response = client.post("/photo/preview", data={"photo_id": "wide"})
        assert "MANUAL PREVIEW" in response.text
        assert 'data-frame-transition="manual-preview"' in response.text
        assert "Manual preview selected — frame unchanged" in response.text
        assert "Show now" in response.text
        assert runtime.repository.load().frame.starting_photo_id == original_start
        response = client.post("/render/start", headers=RENDER_HEADERS)
        assert "Preparing image" in response.text
        assert 'data-frame-transition="updating"' in response.text
        assert "Frame changing now" in response.text
        assert runtime.renderer.state.photo_id == "wide"
        assert runtime.prepared_image is not None
        assert runtime.prepared_image.size == (1200, 800)


def test_render_requires_native_display_dimensions(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        client.post("/connection", data={"server_url": "https://immich.test", "api_key": "secret"})
        client.post("/album/select", data={"album_id": "album"})
        client.post("/photo/preview", data={"photo_id": "wide"})
        response = client.post("/render/start")

    assert "native display width and height" in response.text
    assert 'class="notice error" role="alert"' in response.text
    assert 'data-notice-kind="error"' in response.text
    assert 'aria-label="Dismiss notification"' in response.text


def test_demo_mode_is_local_and_renderable(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        page = client.get("/partials/workspace")
        assert "Quiet places" in page.text
        assert "SELECTED ALBUM" in page.text
        assert "Demo library" in page.text
        assert "Local preview · no network" in page.text
        assert 'name="timezone"' not in page.text
        assert 'class="album-thumbnail" src="/thumbnail/coast"' in page.text
        assert "Northumberland coast.jpg" in page.text
        assert client.get("/thumbnail/coast").headers["content-type"] == "image/svg+xml"


def test_unsupported_assets_are_filtered_once_and_reported_by_category(tmp_path: Path):
    provider = MixedFormatProvider()
    app = create_app(tmp_path, lambda _kind: provider)
    with TestClient(app) as client:
        client.post(
            "/connection",
            data={
                "server_url": "https://immich.test",
                "api_key": "key",  # pragma: allowlist secret
            },
        )
        response = client.post("/album/select", data={"album_id": "album"})
        calls_after_scan = list(provider.original_calls)
        refreshed = client.get("/partials/workspace")
        rejected = client.post("/photo/preview", data={"photo_id": "heic"})

    assert "1 eligible" in response.text
    assert "1 wrong orientation" in response.text
    assert "1 unsupported or unreadable" in response.text
    assert 'aria-label="Preview wide.jpg"' in response.text
    assert 'aria-label="Preview phone.heic"' not in response.text
    assert "phone.heic" not in refreshed.text
    assert provider.original_calls == calls_after_scan
    assert sorted(calls_after_scan) == ["heic", "wide"]
    assert "not eligible or cannot be decoded" in rejected.text


def test_decodable_provider_preview_keeps_heic_original_eligible(tmp_path: Path):
    provider = PreviewFallbackProvider()
    app = create_app(tmp_path, lambda _kind: provider)
    with TestClient(app) as client:
        client.post(
            "/connection",
            data={
                "server_url": "https://immich.test",
                "api_key": "key",  # pragma: allowlist secret
            },
        )
        response = client.post("/album/select", data={"album_id": "album"})
        refreshed = client.get("/partials/workspace")
        previewed = client.post("/photo/preview", data={"photo_id": "heic"})
        app.state.runtime.repository.update(
            lambda settings: settings.device.set_display_size(1200, 800)
        )
        rendered = client.post("/render/start")

    assert "2 eligible" in response.text
    assert "1 wrong orientation" in response.text
    assert "0 unsupported or unreadable" in response.text
    assert 'aria-label="Preview phone.heic"' in response.text
    assert "phone.heic" in refreshed.text
    assert "MANUAL PREVIEW" in previewed.text
    assert "Preparing image" in rendered.text
    assert app.state.runtime.prepared_image is not None
    assert provider.original_calls.count("heic") == 1
    assert provider.thumbnail_calls.count("heic") == 1


def test_ui_acceptance_contracts_are_present(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        page = client.get("/")
        workspace = client.get("/partials/workspace")
        selected_workspace = client.post("/photo/preview", data={"photo_id": "coast"})
        htmx = client.get("/static/vendor/htmx-2.0.4.min.js")
        tab_identity = client.get("/static/tab-identity.js")
        responsive_css = client.get("/static/responsive-fixes.css")
        icon = client.get("/static/photoframe.svg")

    assert "/static/vendor/htmx-2.0.4.min.js" in page.text
    assert "unpkg.com" not in page.text
    assert htmx.status_code == 200
    assert 'version:"2.0.4"' in htmx.text
    assert tab_identity.status_code == 200
    assert "PhotoframeTabIdentity" in tab_identity.text
    assert "createBrowserSession" in page.text
    assert "randomUUID: () => self.crypto.randomUUID()" not in page.text
    assert 'data-theme-choice="light" aria-pressed="false"' in page.text
    assert 'data-theme-choice="dark" aria-pressed="true"' in page.text
    assert "setupNotifications" in page.text
    assert "setupAlbumSelection" in page.text
    assert ".notification-stack" in responsive_css.text
    assert 'rel="icon" href="http://testserver/static/photoframe.svg"' in page.text
    assert 'class="brand-mark"><img src="http://testserver/static/photoframe.svg"' in page.text
    assert 'class="source-state"><span class="status-dot"' in workspace.text
    assert ".source-state" in responsive_css.text
    assert "data-settings-accordion" in workspace.text
    assert 'name="frame-settings" data-settings-panel="provider"' in workspace.text
    assert 'name="frame-settings" data-settings-panel="album"' in workspace.text
    assert 'name="frame-settings" data-settings-panel="display"' in workspace.text
    assert workspace.text.count('class="disclosure-icon"') == 5
    assert "setupSettingsAccordion" in page.text
    assert "activeSettingsPanel" in page.text
    assert "/static/tab-identity.js" in page.text
    assert "htmx:configRequest" in page.text
    assert ".setting-card[open]" in responsive_css.text
    assert icon.status_code == 200
    assert 'id="mdi-image-frame"' in icon.text
    assert 'action="/photo/start"' in selected_workspace.text
    assert "Start rotation here" in selected_workspace.text
    assert "Advanced / manual hardware settings" in workspace.text
    assert '<details class="advanced-settings span-2">' in workspace.text
    assert '<details class="advanced-settings span-2" open' not in workspace.text
    for name in (
        "display_driver",
        "display_model",
        "display_width_px",
        "display_height_px",
        "expected_refresh_seconds",
        "render_timeout_seconds",
    ):
        assert f'name="{name}"' in workspace.text
    assert 'data-settings-panel="advanced"' in workspace.text
    assert 'data-settings-panel="display" data-default-open="false"' in workspace.text
    assert 'data-settings-panel="display" data-default-open="false" open' not in workspace.text
    assert "Advanced settings" in workspace.text
    assert "Every 1 hour · In album order · Landscape" in workspace.text
    assert "Simulator · 24-hour" not in workspace.text
    assert "Network & web security" in workspace.text
    assert "This device only" in workspace.text
    assert "Devices on my local network" in workspace.text
    assert 'name="network_access" value="local_network"' in workspace.text
    assert "home_network" not in workspace.text
    assert "home_network" not in page.text
    assert (
        "Devices on this network can connect through this device\u2019s local IP address"
        in workspace.text
    )
    assert "home network" not in workspace.text.lower()
    assert "Trusted LAN" not in workspace.text
    assert 'name="web_protocol" value="http"' in workspace.text
    assert 'name="web_protocol" value="https"' in workspace.text
    assert "data-certificate-controls hidden" in workspace.text
    assert "Address after restart" in workspace.text
    assert "setupNetworkSettings" in page.text
    assert "confirmNetworkChange" in page.text
    assert 'class="advanced-card-content advanced-card-body"' in workspace.text
    assert 'id="network-port"' in workspace.text
    assert 'aria-describedby="network-port-help"' in workspace.text
    assert 'aria-describedby="certificate-path-help"' in workspace.text
    assert 'aria-describedby="private-key-path-help"' in workspace.text
    assert ".advanced-card-body" in responsive_css.text
    assert "padding: 26px 32px" in responsive_css.text
    assert ".network-field > input" in responsive_css.text
    assert "@media (max-width: 700px)" in responsive_css.text
    assert "Reset Photoframe to defaults?" in workspace.text
    assert "Keep current settings" in workspace.text
    assert "Reset to defaults" in workspace.text
    assert 'name="photo_order"' in workspace.text
    assert "Controls future scheduled changes" in workspace.text


def test_album_picker_is_local_until_confirm_and_exposes_accessible_states(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        client.post("/connection", data={"server_url": "https://immich.test", "api_key": "secret"})
        page = client.get("/")
        workspace = client.get("/partials/workspace")
        before = app.state.runtime.repository.load()

    assert app.state.runtime.repository.load() == before
    assert '<fieldset class="album-list" data-album-picker' in workspace.text
    assert '<legend class="visually-hidden">Choose an album to review</legend>' in workspace.text
    assert 'type="radio" name="pending_album" value="album"' in workspace.text
    assert "data-album-confirmation hidden" in workspace.text
    assert (
        'data-album-announcement role="status" aria-live="polite" aria-atomic="true"'
        in workspace.text
    )
    announcement = workspace.text.index("data-album-announcement")
    confirmation = workspace.text.index("data-album-confirmation")
    assert announcement < confirmation
    assert "data-album-cancel>Cancel</button>" in workspace.text
    assert 'action="/album/select"' in workspace.text
    assert workspace.text.count('action="/album/select"') == 1
    assert "setAlbumBusy(true)" in page.text
    assert "picker.disabled = busy" in page.text
    assert "cancelButton.disabled = busy" in page.text
    assert "Switching album…" in page.text
    assert "focusTarget?.focus({ preventScroll: true })" in page.text
    assert "htmx:afterRequest" in page.text


def test_album_confirm_is_the_only_route_mutation_point_and_marks_current(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        client.post("/connection", data={"server_url": "https://immich.test", "api_key": "secret"})
        before = app.state.runtime.repository.load()
        assert before.frame.album_id is None

        response = client.post("/album/select", data={"album_id": "album"})

    saved = app.state.runtime.repository.load()
    assert saved.frame.album_id == "album"
    assert saved.frame.album_name == "Family"
    assert 'data-current-album-id="album"' in response.text
    assert 'class="album-choice chosen"' in response.text
    assert "<b data-album-state>Current</b>" in response.text


def test_album_confirm_keeps_the_picture_currently_represented_on_frame(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    runtime = app.state.runtime
    old_photo = Photo(id="old", filename="still-on-frame.jpg", width=1200, height=800)
    runtime.repository.update(
        lambda settings: (
            setattr(settings.frame, "album_id", "old-album"),
            setattr(settings.frame, "album_name", "Old album"),
            setattr(settings.verification, "ok", True),
        )
    )
    runtime.albums = [Album(id="album", name="Family")]
    runtime.photos = [old_photo]
    runtime.loaded = True
    runtime.renderer.last_rendered_photo_id = old_photo.id

    with TestClient(app) as client:
        response = client.post("/album/select", data={"album_id": "album"})

    assert "still-on-frame.jpg" in response.text
    assert runtime.repository.load().frame.album_id == "album"
    assert runtime.renderer.rendered_photo_id() == "old"


def test_missing_current_album_is_reported_without_automatic_replacement(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    runtime = app.state.runtime
    runtime.repository.update(
        lambda settings: (
            setattr(settings.frame, "album_id", "gone"),
            setattr(settings.frame, "album_name", "Old family album"),
            setattr(settings.verification, "ok", True),
        )
    )
    runtime.loaded = True
    runtime.albums = [Album(id="album", name="Family")]

    with TestClient(app) as client:
        response = client.get("/partials/workspace")

    assert "Old family album · Unavailable" in response.text
    assert "will remain selected until you confirm another album" in response.text
    assert runtime.repository.load().frame.album_id == "gone"


def test_network_change_requires_confirmation_and_requests_restart(tmp_path: Path):
    from photoframe.settings import SettingsRepository

    restarts: list[bool] = []
    app = create_app(tmp_path, demo_mode=True, restart_callback=lambda: restarts.append(True))
    payload = {
        "network_access": "local_network",
        "network_port": "8443",
        "web_protocol": "http",
        "certificate_mode": "automatic",
    }
    with TestClient(app) as client:
        rejected = client.post("/network", data=payload)
        accepted = client.post("/network", data={**payload, "confirm_endpoint_change": "yes"})

    assert "Confirm the listener change" in rejected.text
    assert "http://&lt;device-ip&gt;:8443" in accepted.text
    assert restarts == [True]
    saved = SettingsRepository(tmp_path).load().network
    assert saved.bind_address == "0.0.0.0"
    assert saved.port == 8443


def test_network_port_validation_is_user_facing(tmp_path: Path):
    from photoframe.settings import SettingsRepository

    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        response = client.post(
            "/network",
            data={
                "network_access": "device_only",
                "network_port": "70000",
                "web_protocol": "http",
                "certificate_mode": "automatic",
                "confirm_endpoint_change": "yes",
            },
        )

    assert "Network settings were not saved" in response.text
    assert "Listening port must be between 1 and 65535" in response.text
    assert SettingsRepository(tmp_path).load().network.port == 8000


def test_automatic_https_generates_private_material_outside_ui(tmp_path: Path):
    from photoframe.settings import SettingsRepository

    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        response = client.post(
            "/network",
            data={
                "network_access": "device_only",
                "network_port": "8000",
                "web_protocol": "https",
                "certificate_mode": "automatic",
                "confirm_endpoint_change": "yes",
            },
        )

    private_key = tmp_path / "tls" / "photoframe-local.key"
    assert private_key.is_file()
    assert "BEGIN PRIVATE KEY" not in response.text  # pragma: allowlist secret
    assert "photoframe-local.key" not in response.text
    assert SettingsRepository(tmp_path).load().network.protocol.value == "https"


def test_invalid_supplied_tls_files_do_not_replace_saved_configuration(tmp_path: Path):
    from photoframe.settings import SettingsRepository

    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        response = client.post(
            "/network",
            data={
                "network_access": "device_only",
                "network_port": "8443",
                "web_protocol": "https",
                "certificate_mode": "supplied",
                "certificate_path": str(tmp_path / "missing.crt"),
                "private_key_path": str(tmp_path / "missing.key"),
                "confirm_endpoint_change": "yes",
            },
        )

    assert "Certificate file does not exist" in response.text
    assert SettingsRepository(tmp_path).load().network == AppSettings().network


def test_valid_supplied_tls_files_are_preserved(tmp_path: Path):
    from photoframe.tls import generate_local_certificate

    certificate, key = generate_local_certificate(tmp_path / "supplied")
    before = (certificate.read_bytes(), key.read_bytes())
    app = create_app(tmp_path / "app", demo_mode=True)
    with TestClient(app) as client:
        response = client.post(
            "/network",
            data={
                "network_access": "local_network",
                "network_port": "8443",
                "web_protocol": "https",
                "certificate_mode": "supplied",
                "certificate_path": str(certificate),
                "private_key_path": str(key),
                "confirm_endpoint_change": "yes",
            },
        )

    assert "Network settings saved" in response.text
    assert (certificate.read_bytes(), key.read_bytes()) == before


def test_shuffle_setting_persists_and_start_here_anchors_new_round(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        response = client.post(
            "/workflow",
            data={
                "orientation": "landscape",
                "rotation_seconds": "300",
                "photo_order": "shuffle",
                "display_width_px": "1200",
                "display_height_px": "750",
                "expected_refresh_seconds": "28",
                "render_timeout_seconds": "90",
                "display_driver": "mock",
            },
        )
        assert "Scheduled shuffle" in response.text
        assert "Every 5 minutes · Shuffle · Landscape" in response.text
        runtime = app.state.runtime
        saved = runtime.repository.load()
        first_deck = saved.frame.shuffle_photo_ids
        assert saved.frame.photo_order == PhotoOrder.SHUFFLE
        assert len(first_deck) == 5

        client.post("/photo/preview", data={"photo_id": "coast"})
        response = client.post("/photo/start", data={"photo_id": "coast"})
        saved = runtime.repository.load()
        assert "new shuffled round" in response.text
        assert saved.frame.shuffle_photo_ids[0] == "coast"
        assert len(set(saved.frame.shuffle_photo_ids)) == 5


def test_reset_clears_configuration_secret_cache_and_runtime(tmp_path: Path):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        client.post("/connection", data={"server_url": "https://immich.test", "api_key": "secret"})
        client.post("/album/select", data={"album_id": "album"})
        runtime = app.state.runtime
        runtime.cache.put("immich:wide", b"cached-photo")
        assert runtime.cache.stats().files == 1

        response = client.post("/reset")

    assert "Photoframe was reset to defaults" in response.text
    defaults = runtime.repository.load()
    assert defaults.provider == AppSettings().provider
    assert defaults.frame.album_id is None
    assert defaults.frame.photo_order == PhotoOrder.ALBUM
    assert defaults.device == AppSettings().device
    assert not runtime.secrets.exists()
    assert not runtime.secrets.key_path.exists()
    assert runtime.cache.stats().files == 0
    assert runtime.catalog_snapshot() == ([], [])
    assert runtime.preview_id() is None


def test_reset_truthfully_reports_partial_cache_cleanup(tmp_path: Path, monkeypatch):
    app = create_app(tmp_path, lambda _kind: FakeProvider())
    with TestClient(app) as client:
        client.post("/connection", data={"server_url": "https://immich.test", "api_key": "secret"})
        runtime = app.state.runtime
        monkeypatch.setattr(runtime.cache, "clear", lambda: (_ for _ in ()).throw(OSError()))
        response = client.post("/reset")

    assert "Reset incomplete" in response.text
    defaults = runtime.repository.load()
    assert defaults.provider == AppSettings().provider
    assert defaults.frame.album_id is None


def test_background_polling_never_replaces_the_settings_workspace(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        workspace = client.get("/partials/workspace")
        client.post("/photo/preview", data={"photo_id": "coast"})
        render_started = client.post("/render/start")
        frame_status = client.get("/partials/frame-status")
        render_status = client.get("/partials/render-status")

    workspace_tag = workspace.text.split(">", 1)[0]
    assert workspace_tag == '<div id="workspace" class="workspace"'
    assert 'id="frame-status"' in workspace.text
    assert 'hx-get="/partials/frame-status"' in frame_status.text
    assert 'hx-trigger="every 1s"' in frame_status.text
    assert 'id="workspace"' not in frame_status.text
    assert 'id="render-status"' in render_started.text
    assert 'hx-get="/partials/render-status"' in render_started.text
    assert 'hx-trigger="every 1s"' in render_started.text
    assert 'hx-target="#workspace"' not in render_status.text
    assert 'id="workspace"' not in render_status.text


def test_scheduled_transition_is_prominent_in_the_frame_preview(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    now = datetime.now(UTC)
    app.state.runtime.repository.update(
        lambda settings: (
            setattr(settings.frame, "rotation_seconds", 30),
            setattr(settings.frame, "schedule_anchor", now),
        )
    )

    with TestClient(app) as client:
        response = client.get("/partials/frame-status")

    assert 'data-frame-transition="scheduled"' in response.text
    assert "Scheduled change approaching" in response.text
    assert 'role="status" aria-live="polite"' in response.text
    assert 'hx-trigger="every 1s"' in response.text


def test_successful_render_auto_dismisses_but_failure_remains(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        client.post("/photo/preview", data={"photo_id": "coast"})
        client.post("/render/start", headers=RENDER_HEADERS)
        runtime = app.state.runtime
        started = runtime.renderer.snapshot().started_at
        assert started is not None
        settings = runtime.repository.load()
        runtime.renderer.update(
            settings.device,
            started + timedelta(seconds=settings.device.expected_refresh_seconds),
        )
        completed = client.get("/partials/render-status", headers=RENDER_HEADERS)

        assert 'data-auto-dismiss-render="6000"' in completed.text
        assert 'hx-post="/render/dismiss"' in completed.text
        assert 'hx-trigger="load delay:6000ms"' in completed.text
        assert "Dismiss now" in completed.text

        dismissed = client.post("/render/dismiss")
        assert dismissed.text == ""
        assert runtime.renderer.snapshot().phase.value == "idle"
        assert runtime.preview_id() is None

        runtime.repository.update(
            lambda saved: (
                setattr(saved.device, "expected_refresh_seconds", 50),
                setattr(saved.device, "render_timeout_seconds", 10),
            )
        )
        now = datetime.now(UTC)
        runtime.renderer.start("coast", now)
        runtime.renderer.update(runtime.repository.load().device, now + timedelta(seconds=10))
        failed = client.get("/partials/render-status")

    assert "Frame update failed" in failed.text
    assert "data-auto-dismiss-render" not in failed.text
    assert 'hx-post="/render/dismiss"' not in failed.text
    assert ">Done<" in failed.text


def test_manual_render_popup_is_correlated_to_the_initiating_browser(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    other_browser = {"X-Photoframe-Render-Intent": "a70cc8a8-fbd9-4b98-8290-011d0eabfa06"}
    with TestClient(app) as client:
        client.post("/photo/preview", data={"photo_id": "coast"})
        initiated = client.post("/render/start", headers=RENDER_HEADERS)
        same_browser = client.get("/partials/frame-status", headers=RENDER_HEADERS)
        unrelated_browser = client.get("/partials/frame-status", headers=other_browser)
        unrelated_workspace = client.get("/partials/workspace", headers=other_browser)

    assert 'data-frame-transition="updating"' in initiated.text
    assert 'data-frame-transition="updating"' in same_browser.text
    assert 'data-frame-transition="updating"' not in unrelated_browser.text
    assert 'data-frame-transition="manual-preview"' not in unrelated_browser.text
    assert "Updating the frame" in initiated.text
    assert 'id="render-status"' in unrelated_workspace.text


def test_completed_render_visibility_uses_fixed_server_deadlines(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    settings = runtime.repository.load()
    now = datetime.now(UTC)

    with TestClient(app) as client:
        client.post("/photo/preview", data={"photo_id": "coast"})

        recently_started = now - timedelta(seconds=settings.device.expected_refresh_seconds + 3)
        runtime.renderer.start("coast", recently_started, operation_id=RENDER_INTENT)
        runtime.renderer.update(
            settings.device,
            recently_started + timedelta(seconds=settings.device.expected_refresh_seconds),
        )
        recent = client.get("/partials/workspace", headers=RENDER_HEADERS)
        recent_other_browser = client.get(
            "/partials/workspace",
            headers={"X-Photoframe-Render-Intent": "a70cc8a8-fbd9-4b98-8290-011d0eabfa06"},
        )

        assert 'data-frame-transition="complete"' in recent.text
        assert 'data-auto-dismiss-render="6000"' not in recent.text
        assert "data-auto-dismiss-render=" in recent.text
        assert 'id="render-status"' not in recent_other_browser.text
        assert runtime.renderer.snapshot().phase.value == "complete"

        ten_seconds_ago = datetime.now(UTC) - timedelta(
            seconds=settings.device.expected_refresh_seconds + 10
        )
        runtime.renderer.reset()
        runtime.renderer.start("coast", ten_seconds_ago, operation_id=RENDER_INTENT)
        runtime.renderer.update(
            settings.device,
            ten_seconds_ago + timedelta(seconds=settings.device.expected_refresh_seconds),
        )
        reloaded = client.get("/partials/workspace", headers=RENDER_HEADERS)

        assert 'data-frame-transition="complete"' not in reloaded.text
        assert 'id="render-status"' not in reloaded.text
        assert runtime.renderer.snapshot().phase.value == "complete"

        stale_started = datetime.now(UTC) - timedelta(
            seconds=settings.device.expected_refresh_seconds + 31
        )
        runtime.renderer.reset()
        runtime.renderer.start("coast", stale_started, operation_id=RENDER_INTENT)
        runtime.renderer.update(
            settings.device,
            stale_started + timedelta(seconds=settings.device.expected_refresh_seconds),
        )
        expired = client.get("/partials/workspace", headers=RENDER_HEADERS)

    assert 'data-frame-transition="complete"' not in expired.text
    assert 'id="render-status"' not in expired.text
    assert runtime.renderer.snapshot().phase.value == "idle"
    assert runtime.preview_id() is None
