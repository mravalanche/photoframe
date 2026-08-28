from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from photoframe.models import Album, Photo
from photoframe.web import create_app


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

    def thumbnail(self, photo_id):
        return b"image", "image/jpeg"

    def original(self, photo_id):
        image = Image.new("RGB", (1200, 800), "navy")
        output = BytesIO()
        image.save(output, format="JPEG")
        return output.getvalue(), "image/jpeg"


def test_complete_local_web_flow(tmp_path: Path):
    app = create_app(tmp_path, lambda _url, _key: FakeProvider())
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        response = client.post(
            "/connection", data={"server_url": "https://immich.test", "api_key": "secret"}
        )
        assert response.status_code == 200
        assert "Connected for test" in response.text
        assert "secret" not in response.text
        response = client.post("/album/select", data={"album_id": "album"})
        assert "1 of 2 images eligible" in response.text
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
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"


def test_preview_does_not_change_schedule_until_explicit_actions(tmp_path: Path):
    app = create_app(tmp_path, lambda _url, _key: FakeProvider())
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
        assert "SELECTED PREVIEW" in response.text
        assert "Show now" in response.text
        assert runtime.repository.load().frame.starting_photo_id == original_start
        response = client.post("/render/start")
        assert "Preparing image" in response.text
        assert runtime.renderer.state.photo_id == "wide"
        assert runtime.prepared_image is not None
        assert runtime.prepared_image.size == (1200, 800)


def test_render_requires_native_display_dimensions(tmp_path: Path):
    app = create_app(tmp_path, lambda _url, _key: FakeProvider())
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


def test_ui_acceptance_contracts_are_present(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        page = client.get("/")
        workspace = client.get("/partials/workspace")
        selected_workspace = client.post("/photo/preview", data={"photo_id": "coast"})
        htmx = client.get("/static/vendor/htmx-2.0.4.min.js")
        responsive_css = client.get("/static/responsive-fixes.css")
        icon = client.get("/static/photoframe.svg")

    assert "/static/vendor/htmx-2.0.4.min.js" in page.text
    assert "unpkg.com" not in page.text
    assert htmx.status_code == 200
    assert 'version:"2.0.4"' in htmx.text
    assert 'data-theme-choice="light" aria-pressed="false"' in page.text
    assert 'data-theme-choice="dark" aria-pressed="true"' in page.text
    assert "setupNotifications" in page.text
    assert ".notification-stack" in responsive_css.text
    assert 'rel="icon" href="http://testserver/static/photoframe.svg"' in page.text
    assert 'class="brand-mark"><img src="http://testserver/static/photoframe.svg"' in page.text
    assert 'class="source-state"><span class="status-dot"' in workspace.text
    assert ".source-state" in responsive_css.text
    assert "data-settings-accordion" in workspace.text
    assert 'name="frame-settings" data-settings-panel="provider"' in workspace.text
    assert 'name="frame-settings" data-settings-panel="album"' in workspace.text
    assert 'name="frame-settings" data-settings-panel="display"' in workspace.text
    assert workspace.text.count('class="disclosure-icon"') == 4
    assert "setupSettingsAccordion" in page.text
    assert "activeSettingsPanel" in page.text
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
    assert 'hx-trigger="every 30s"' in frame_status.text
    assert 'id="render-status"' in render_started.text
    assert 'hx-get="/partials/render-status"' in render_started.text
    assert 'hx-trigger="every 1s"' in render_started.text
    assert 'hx-target="#workspace"' not in render_status.text
