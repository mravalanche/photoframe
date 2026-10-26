"""HTTP monitor contracts stay distinct from hardware readiness."""

import pytest
from fastapi.testclient import TestClient

from photoframe import __version__
from photoframe.web import create_app


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_health_and_liveness_support_http_monitors(tmp_path, method):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    health = client.request(method, "/health")
    live = client.request(method, "/health/live")
    assert health.status_code == live.status_code == 200
    assert health.headers["cache-control"] == live.headers["cache-control"] == "no-store"
    if method == "HEAD":
        assert health.content == live.content == b""
    else:
        assert health.json()["status"] == "healthy"
        assert live.json() == {"status": "alive", "version": __version__}


def test_failed_display_is_degraded_while_web_process_remains_alive(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    app.state.runtime.repository.update(
        lambda saved: setattr(
            saved.refresh_status,
            "last_render_error",
            "Pimoroni Inky failed to refresh: empty palette",
        )
    )
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == client.head("/health").status_code == 503
    assert response.json()["status"] == "degraded"
    assert "empty palette" in response.json()["detail"]
    assert response.json()["checks"]["catalog"]["status"] == "healthy"
    assert response.json()["checks"]["render"]["status"] == "failed"
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/update").json() == {"version": __version__, "ready": True}
    update_probe = client.head("/health/update")
    assert update_probe.status_code == 200
    assert update_probe.content == b""
    assert update_probe.headers["cache-control"] == "no-store"


def test_liveness_does_not_require_working_settings_or_provider(tmp_path, monkeypatch):
    app = create_app(tmp_path, demo_mode=True)

    def unavailable(*_args, **_kwargs):
        raise OSError("Settings storage unavailable")

    monkeypatch.setattr(app.state.runtime.repository, "load", unavailable)
    client = TestClient(app)
    assert client.get("/health/live").status_code == 200
    assert client.head("/health/live").status_code == 200


def test_documented_read_endpoints_respond_with_demo_data(tmp_path, monkeypatch):
    app = create_app(tmp_path, demo_mode=True)
    monkeypatch.setattr(
        app.state.updater, "check_public_release", lambda: {"version": "2.0.0.dev1"}
    )
    client = TestClient(app)
    for path in (
        "/",
        "/photos",
        "/settings",
        "/settings/provider",
        "/settings/hardware",
        "/settings/advanced",
        "/settings/updates",
        "/partials/workspace",
        "/partials/workspace?view=photos",
        "/partials/frame-status",
        "/partials/render-status",
        "/api/activity",
        "/api/updates/status",
        "/api/updates/releases",
        "/health/update",
        "/thumbnail/coast",
        "/thumbnail/coast?size=preview",
        "/photos/coast/prepared",
        "/docs",
        "/openapi.json",
    ):
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text[:200]}"
