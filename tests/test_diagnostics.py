"""Persistent operational diagnostics stay bounded, useful and safe to share."""

import errno
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from photoframe.diagnostics import MAX_EVENTS, MAX_FILE_BYTES, DiagnosticLog, failure_detail
from photoframe.providers import ProviderError
from photoframe.web import create_app


def test_history_survives_restart_and_is_bounded_and_private(tmp_path):
    log = DiagnosticLog(tmp_path)
    for _ in range(MAX_EVENTS + 2):
        log.record("catalog.refreshed")
    log.record("display.failed", "SPI connection failed: private-photo.jpg")
    restarted = DiagnosticLog(tmp_path)
    events = restarted.recent(limit=MAX_EVENTS)
    assert len(events) == MAX_EVENTS
    assert events[0]["code"] == "display.failed"
    assert "panel connection" in events[0]["detail"]
    assert restarted.path.stat().st_size < MAX_FILE_BYTES
    assert restarted.path.stat().st_mode & 0o777 == 0o600
    assert "private-photo.jpg" not in restarted.path.read_text()


def test_error_details_keep_status_and_transport_cause_without_payloads(tmp_path):
    request = httpx.Request("GET", "https://user:password@private.example/photo?token=private")
    response = httpx.Response(401, request=request)
    status = httpx.HTTPStatusError("sensitive response body", request=request, response=response)
    outer = ProviderError("provider API key is private")
    outer.__cause__ = status
    assert failure_detail(outer) == "Photo provider returned HTTP 401."
    assert "timed out" in failure_detail(httpx.ReadTimeout("private token", request=request))
    assert "Wi-Fi, DNS" in failure_detail(httpx.ConnectError("private host", request=request))
    assert "ENOSPC" in failure_detail(OSError(errno.ENOSPC, "private file path"))
    log = DiagnosticLog(tmp_path)
    for error in (outer, status, ValueError("private secret"), "Bearer private\nAuthorization"):
        log.record("worker.failed", error)
    exported = log.text()
    for sensitive in ("private", "password", "Authorization", "sensitive response"):
        assert sensitive not in exported


@pytest.mark.parametrize("payload", [b"broken", b"{}", b"x" * (MAX_FILE_BYTES + 1)])
def test_corrupt_or_oversized_history_is_recoverable(tmp_path, payload):
    (tmp_path / "diagnostics.json").write_bytes(payload)
    log = DiagnosticLog(tmp_path)
    assert log.storage_error
    assert log.recent() == []
    log.record("app.started")
    assert not log.storage_error
    assert DiagnosticLog(tmp_path).recent()[0]["code"] == "app.started"


def test_unwritable_history_does_not_break_operation_and_warns(tmp_path, monkeypatch):
    log = DiagnosticLog(tmp_path)

    def fail_write(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left")

    monkeypatch.setattr("photoframe.diagnostics.atomic_write", fail_write)
    log.record("worker.failed", RuntimeError("private context"))
    assert log.storage_error
    assert log.recent()[0]["code"] == "worker.failed"


def test_restart_does_not_trust_arbitrary_historic_detail(tmp_path):
    log = DiagnosticLog(tmp_path)
    log.record("worker.failed", OSError(errno.ENOSPC, "private path"))
    log.record("provider.failed", "Immich returned 403")
    entries = json.loads(log.path.read_text())
    entries.append(
        {**entries[0], "detail": 'private credential\n<script>alert("private")</script>'}
    )
    log.path.write_text(json.dumps(entries))
    restarted = DiagnosticLog(tmp_path)
    assert "private" not in restarted.text()
    assert "<script>" not in restarted.text()
    assert "ENOSPC" in restarted.text()
    assert "HTTP 403" in restarted.text()


def test_advanced_log_filter_download_and_maintenance_access(tmp_path, monkeypatch):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    runtime.diagnostics.record("app.started")
    runtime.diagnostics.record("worker.failed", httpx.ReadTimeout("private token"))
    runtime.diagnostics.record("display.unavailable")

    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnostic views must not contact the provider")

    monkeypatch.setattr(runtime, "refresh_albums", forbidden)
    client = TestClient(app)
    view = client.get("/partials/workspace?section=advanced")
    assert view.status_code == 200
    assert 'id="diagnostics-title"' in view.text
    assert "Photoframe started" in view.text
    assert view.headers["cache-control"] == "no-store"
    filtered = client.get("/partials/diagnostics?level=errors")
    assert "Background refresh worker failed" in filtered.text
    assert "Physical display unavailable" in filtered.text
    assert "Photoframe started" not in filtered.text
    assert "private token" not in filtered.text
    assert filtered.headers["cache-control"] == "no-store"
    runtime.maintenance_gate.enter_and_drain(0)
    assert client.get("/partials/diagnostics").status_code == 200
    download = client.get("/diagnostics/download")
    assert download.status_code == 200
    assert (
        'attachment; filename="photoframe-diagnostics.txt"'
        in download.headers["content-disposition"]
    )
    assert download.headers["cache-control"] == "no-store"
    assert "app.started" in download.text and "worker.failed" in download.text


def test_worker_failure_events_persist_with_safe_details(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    app.state.runtime.record_worker_failure(httpx.ConnectError("private host"))
    events = DiagnosticLog(tmp_path).recent(failures_only=True)
    assert events[0]["code"] == "worker.failed"
    assert "Wi-Fi, DNS" in events[0]["detail"]
    assert "private host" not in json.dumps(events)


def test_unexpected_request_failure_is_logged_without_url_or_body(tmp_path):
    app = create_app(tmp_path, demo_mode=True)

    @app.get("/test-fault")
    def fault():
        raise RuntimeError("private payload")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/test-fault?token=private").status_code == 500
    event = app.state.runtime.diagnostics.recent()[0]
    assert event["code"] == "request.failed"
    assert "private" not in app.state.runtime.diagnostics.text()


def test_catalog_failure_and_retry_recovery_are_recorded(tmp_path, monkeypatch):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    original = runtime.refresh_photos
    current = datetime.now(UTC) + timedelta(days=1)

    def fail_refresh():
        try:
            raise httpx.ReadTimeout("private server and token")
        except httpx.ReadTimeout as exc:
            raise ProviderError("private upstream context") from exc

    monkeypatch.setattr(runtime, "refresh_photos", fail_refresh)
    assert runtime.refresh_lifecycle(current)
    event = runtime.diagnostics.recent()[0]
    assert event["code"] == "catalog.failed"
    assert "timed out" in event["detail"]
    monkeypatch.setattr(runtime, "refresh_photos", original)
    retry = runtime.repository.load().refresh_status.next_attempt_at
    assert runtime.refresh_lifecycle(retry)
    assert runtime.diagnostics.recent()[0]["code"] == "catalog.refreshed"
    assert "private" not in DiagnosticLog(tmp_path).text()


def test_process_lifecycle_events_survive_recreation(tmp_path, monkeypatch):
    monkeypatch.setattr("photoframe.web.routes.RefreshWorker.start", lambda self: None)
    monkeypatch.setattr("photoframe.web.routes.RefreshWorker.stop", lambda self: None)
    with TestClient(create_app(tmp_path, demo_mode=True)):
        assert DiagnosticLog(tmp_path).recent()[0]["code"] == "app.started"
    assert DiagnosticLog(tmp_path).recent()[0]["code"] == "app.stopped"
