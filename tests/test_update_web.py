from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from photoframe import __version__
from photoframe.updater.auth import PinStore
from photoframe.updater.managed import ManagedInstallation
from photoframe.web import create_app


@pytest.fixture
def updater(tmp_path, monkeypatch):
    pin = tmp_path / "pin"
    PinStore(pin).establish("test-123456")
    monkeypatch.setenv("PHOTOFRAME_UPDATE_PIN_FILE", str(pin))
    app = create_app(tmp_path)
    controller = app.state.updater
    controller.installation = ManagedInstallation(True, "Managed", tmp_path)
    controller.helper = Mock()
    controller.helper.call.return_value = {"ok": True, "phase": "idle", "message": "Ready"}
    return app, controller


def login(client):
    response = client.post(
        "/api/updates/login", json={"pin": "test-123456"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf"]}


def test_auth_origin_csrf_and_rate_limit(updater):
    app, controller = updater
    client = TestClient(app)
    assert client.post("/api/updates/login", json={"pin": "test-123456"}).status_code == 403
    assert (
        client.post(
            "/api/updates/login",
            json={"pin": "test-123456"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    headers = login(client)
    assert (
        client.post(
            "/api/updates/check", json={}, headers={"Origin": "http://testserver"}
        ).status_code
        == 403
    )
    assert client.post("/api/updates/check", json={}, headers=headers).status_code == 200
    assert controller.helper.call.call_args.args[0] == "check"
    for _ in range(5):
        assert (
            client.post(
                "/api/updates/login",
                json={"pin": "wrong-pin"},
                headers={"Origin": "http://testserver"},
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/updates/login",
            json={"pin": "test-123456"},
            headers={"Origin": "http://testserver"},
        ).status_code
        == 429
    )


def test_explicit_apply_and_maintenance(updater):
    app, controller = updater
    client = TestClient(app)
    headers = login(client)
    assert (
        client.post("/api/updates/activate", json={"release": "1.3.0"}, headers=headers).status_code
        == 400
    )
    controller.helper.call.return_value = {"ok": True, "accepted": True, "phase": "activating"}
    response = client.post(
        "/api/updates/activate", json={"release": "1.3.0", "confirmed": True}, headers=headers
    )
    assert response.status_code == 200
    assert app.state.runtime.maintenance_gate.maintenance
    assert client.post("/album/refresh").status_code == 503
    assert app.state.runtime.refresh_lifecycle() is False
    assert client.get("/health/update").json() == {"version": __version__, "ready": True}


def test_failed_apply_releases_gate(updater):
    app, controller = updater
    controller.helper.call.return_value = {"ok": False, "message": "Not staged"}
    with pytest.raises(ValueError, match="Not staged"):
        controller.action("activate", "1.3.0")
    assert not app.state.runtime.maintenance_gate.maintenance


def test_weekly_preference_survives_restart(updater):
    app, controller = updater
    client = TestClient(app)
    assert (
        client.post(
            "/api/updates/preferences", json={"weekly": False}, headers=login(client)
        ).status_code
        == 200
    )
    another = create_app(controller.path.parent).state.updater
    assert another.preferences["weekly"] is False
    assert another.tick() is False
    controller.preferences.update(weekly=True, next_check=0)
    assert controller.tick() is True
    assert controller.preferences["next_check"] > 0
    assert controller.tick() is False


def test_unmanaged_no_mutation_and_accessible_page(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/updates/status").json()["managed"] is False
    with pytest.raises(ValueError, match="managed installation"):
        app.state.updater.action("stage", "1.3.0")
    html = client.get("/updates").text
    assert 'role="status"' in html
    assert "Administrator update PIN" in html
    assert "Apply &amp;" in html or "Apply & restart" in html
    assert "Software updates" in client.get("/").text


def test_ambiguous_activation_is_durable_and_tick_recovers(updater):
    app, controller = updater
    controller.helper.call.side_effect = TimeoutError("connection lost after acceptance")
    job = "f" * 32
    with pytest.raises(TimeoutError):
        controller.action("activate", "1.3.0", job)
    assert controller.activation_job == job
    assert app.state.runtime.maintenance_gate.maintenance
    controller.preferences["weekly"] = False
    controller.save()
    another = create_app(controller.path.parent).state.updater
    assert another.activation_job == job
    assert another.runtime.maintenance_gate.maintenance
    controller.helper.call.side_effect = None
    controller.helper.call.return_value = {"ok": True, "phase": "failed", "job_id": job}
    controller.tick()
    assert not app.state.runtime.maintenance_gate.maintenance
    assert controller.activation_job is None
    assert "activation_job" not in controller.preferences


def test_activation_retries_same_durable_id_when_acceptance_unknown(updater):
    _app, controller = updater
    controller.helper.call.side_effect = TimeoutError("not accepted")
    job = "e" * 32
    with pytest.raises(TimeoutError):
        controller.action("activate", "1.3.0", job)
    controller.helper.call.side_effect = None
    controller.helper.call.return_value = {"ok": True, "phase": "idle"}
    controller.tick()
    assert controller.helper.call.call_args.args == ("activate", job, "1.3.0")


def test_browser_job_id_and_large_payload(updater):
    app, controller = updater
    client = TestClient(app)
    headers = login(client)
    assert (
        client.post("/api/updates/check", json={"junk": "x" * 4096}, headers=headers).status_code
        == 400
    )
    job = "d" * 32
    response = client.post(
        "/api/updates/activate",
        json={"release": "1.3.0", "confirmed": True, "request_id": job},
        headers=headers,
    )
    assert response.status_code == 200
    assert controller.helper.call.call_args.args == ("activate", job, "1.3.0")
    assert client.get("/partials/workspace").status_code == 503


def test_preparation_failure_never_retries_or_locks_frame(updater, monkeypatch):
    app, controller = updater
    monkeypatch.setattr(controller, "save", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        controller.action("activate", "1.3.0")
    assert not controller.activation_job
    assert not app.state.runtime.maintenance_gate.maintenance
    assert "activation_action" not in controller.preferences
    controller.helper.call.assert_not_called()


def test_invalid_request_id_is_rejected(updater):
    app, _controller = updater
    client = TestClient(app)
    headers = login(client)
    response = client.post("/api/updates/check", json={"request_id": {}}, headers=headers)
    assert response.status_code == 400
