from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from photoframe import __version__
from photoframe.updater.managed import ManagedInstallation
from photoframe.web import create_app


@pytest.fixture
def updater(tmp_path):
    app = create_app(tmp_path)
    controller = app.state.updater
    controller.installation = ManagedInstallation(True, "Managed", tmp_path)
    controller.helper = Mock()
    controller.helper.call.return_value = {"ok": True, "phase": "idle", "message": "Ready"}
    return app, controller


def browser_session(client):
    response = client.post("/api/updates/session", json={}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf"]}


def test_passphrase_free_session_still_requires_origin_and_csrf(updater):
    app, controller = updater
    client = TestClient(app)
    assert client.post("/api/updates/session", json={}).status_code == 403
    assert (
        client.post(
            "/api/updates/session",
            json={},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    headers = browser_session(client)
    assert (
        client.post(
            "/api/updates/check", json={}, headers={"Origin": "http://testserver"}
        ).status_code
        == 403
    )
    assert client.post("/api/updates/check", json={}, headers=headers).status_code == 200
    assert controller.helper.call.call_args.args[0] == "check"


def test_explicit_apply_and_maintenance(updater):
    app, controller = updater
    client = TestClient(app)
    headers = browser_session(client)
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


def test_browser_session_renews_without_credentials_and_ignores_old_pin(tmp_path, monkeypatch):
    old_pin = tmp_path / "update-pin.hash"
    old_pin.write_text("obsolete credential")
    monkeypatch.setenv("PHOTOFRAME_UPDATE_PIN_FILE", str(old_pin))
    app = create_app(tmp_path)
    app.state.updater.installation = ManagedInstallation(True, "Managed", tmp_path)
    app.state.updater.helper = Mock()
    app.state.updater.helper.call.return_value = {"ok": True, "phase": "idle"}
    client = TestClient(app)
    previous = browser_session(client)
    current = browser_session(client)
    assert current["X-CSRF-Token"] != previous["X-CSRF-Token"]
    assert client.post("/api/updates/check", json={}, headers=previous).status_code == 403
    assert client.post("/api/updates/check", json={}, headers=current).status_code == 200
    assert old_pin.read_text() == "obsolete credential"
    assert client.post("/api/updates/logout", json={}, headers=current).status_code == 400


def test_browser_session_rejects_non_json_and_has_secure_cookie(updater):
    app, controller = updater
    client = TestClient(app, base_url="https://testserver")
    assert (
        client.post(
            "/api/updates/session", content="{}", headers={"Origin": "https://testserver"}
        ).status_code
        == 403
    )
    response = client.post(
        "/api/updates/session", json={}, headers={"Origin": "https://testserver"}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    controller.helper.call.assert_not_called()


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
            "/api/updates/preferences", json={"weekly": False}, headers=browser_session(client)
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
    assert "update-pin" not in html
    assert "unlock-form" not in html
    assert "lock-update" not in html
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
    headers = browser_session(client)
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
    headers = browser_session(client)
    response = client.post("/api/updates/check", json={"request_id": {}}, headers=headers)
    assert response.status_code == 400


@pytest.mark.parametrize("release", [None, {}, "1.3.0-rc1"])
def test_invalid_activation_release_never_locks_frame(updater, release):
    app, controller = updater
    with pytest.raises(ValueError):
        controller.action("activate", release)
    assert not app.state.runtime.maintenance_gate.maintenance
    assert controller.activation_job is None
    controller.helper.call.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "phase": "failed", "message": "Request rejected"},
        {"ok": True, "phase": "complete"},
    ],
)
def test_recovery_retry_terminal_response_releases_durable_gate(updater, result):
    app, controller = updater
    controller.helper.call.side_effect = TimeoutError("acceptance unknown")
    with pytest.raises(TimeoutError):
        controller.action("activate", "1.3.0")
    controller.helper.call.side_effect = [{"ok": True, "phase": "idle"}, result]
    controller.tick()
    assert controller.activation_job is None
    assert not app.state.runtime.maintenance_gate.maintenance
    another = create_app(controller.path.parent).state.updater
    assert another.activation_job is None
    assert not another.runtime.maintenance_gate.maintenance


def test_terminal_cleanup_write_failure_keeps_durable_gate(updater, monkeypatch):
    app, controller = updater
    controller.action("activate", "1.3.0")
    job = controller.activation_job
    controller.helper.call.return_value = {"phase": "complete", "job_id": job}
    monkeypatch.setattr(controller, "save", Mock(side_effect=OSError("disk full")))
    assert controller.status()["phase"] == "unavailable"
    assert controller.activation_job == job
    assert controller.preferences["activation_job"] == job
    assert app.state.runtime.maintenance_gate.maintenance


def test_pending_job_id_cannot_be_reused_to_release_gate(updater):
    app, controller = updater
    controller.action("activate", "1.3.0")
    job = controller.activation_job
    controller.helper.call.reset_mock()
    with pytest.raises(ValueError, match="already in progress"):
        controller.action("check", request_id=job)
    with pytest.raises(ValueError, match="already in progress"):
        controller.action("activate", "1.4.0", request_id=job)
    assert controller.activation_job == job
    assert app.state.runtime.maintenance_gate.maintenance
    controller.helper.call.assert_not_called()
