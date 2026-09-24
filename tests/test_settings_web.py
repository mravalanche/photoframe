"""Settings navigation and recovery remain separate from everyday frame controls."""

import re

from fastapi.testclient import TestClient

from photoframe.web import create_app

ORIGIN = {"Origin": "http://testserver"}


def test_settings_navigation_and_legacy_update_link(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    for section in ("updates", "provider", "hardware", "advanced"):
        page = client.get(f"/settings/{section}")
        assert page.status_code == 200
        assert f'href="/settings/{section}" aria-current="page"' in page.text
        assert 'href="/">← Back to frame' in page.text
        assert "/static/settings.css" in page.text
        assert "photoframe-theme" in page.text
    assert client.get("/settings").status_code == 200
    assert client.get("/settings/missing").status_code == 404
    legacy = client.get("/updates", follow_redirects=False)
    assert legacy.headers["location"] == "/settings/updates"
    app.state.runtime.maintenance_gate.enter_and_drain(0)
    assert client.get("/settings/updates").status_code == 200


def test_provider_form_stays_in_settings_after_save(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    response = client.post(
        "/connection",
        headers={"X-Settings-Section": "provider"},
        data={"server_url": "https://example.test", "api_key": ""},
    )
    assert 'data-settings-panel="provider"' in response.text
    assert 'id="frame-status"' not in response.text
    assert "X-Settings-Section" in response.text
    home = client.get("/partials/workspace").text
    assert 'data-settings-panel="provider"' not in home
    assert 'data-settings-panel="advanced"' not in home
    assert 'data-settings-panel="album"' in home
    assert "data-schedule-form" in home


def test_refresh_reloads_thumbnails_without_rendering(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    runtime = app.state.runtime
    before = runtime.repository.load()
    response = client.post("/album/refresh", headers=ORIGIN)
    assert response.status_code == 200
    assert "The picture on your frame and its schedule are unchanged." in response.text
    revision = re.search(r'/thumbnail/[^"?]+\?refresh=([a-f0-9]+)', response.text)
    assert revision
    assert revision[1] in client.get("/partials/frame-status").text
    after = runtime.repository.load()
    assert after.frame.schedule_anchor == before.frame.schedule_anchor
    assert (
        after.refresh_status.last_rendered_photo_id == before.refresh_status.last_rendered_photo_id
    )
    assert not runtime.renderer.snapshot().active


def test_new_expensive_mutations_reject_foreign_or_missing_origin(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    before = app.state.runtime.repository.load()
    for route in ("/album/refresh", "/hardware"):
        assert client.post(route).status_code == 403
        assert client.post(route, headers={"Origin": "https://foreign.test"}).status_code == 403
    assert app.state.runtime.repository.load() == before


def test_source_release_check_reports_timestamp_and_error(tmp_path):
    app = create_app(tmp_path)
    controller = app.state.updater
    controller.public_checked_at = 123456
    controller.last_error = "Could not check releases"
    status = TestClient(app).get("/api/updates/status").json()
    assert status["last_check"] == 123456
    assert status["check_error"] == "Could not check releases"


def test_initial_shell_and_settings_do_not_wait_for_photo_provider(tmp_path, monkeypatch):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime

    def forbidden(*args, **kwargs):
        raise AssertionError("Settings and initial shell must not fetch or validate photos")

    for method in ("refresh_albums", "refresh_photos", "photo_eligibility", "workspace_snapshot"):
        monkeypatch.setattr(runtime, method, forbidden)
    client = TestClient(app)
    initial = client.get("/")
    assert initial.status_code == 200
    assert "Quiet places" in initial.text
    assert "SAVED SCHEDULE" in initial.text
    assert "Try loading again" in initial.text
    for section in ("provider", "hardware", "advanced"):
        assert client.get(f"/partials/workspace?section={section}").status_code == 200
