from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photoframe.models import Album, Photo
from photoframe.providers import ProviderError
from photoframe.web import create_app


class ControlledProvider:
    def __init__(self):
        self.checking = Event()
        self.release = Event()
        self.fail = False
        self.calls = []
        output = BytesIO()
        with Image.new("RGB", (20, 10), "red") as image:
            image.save(output, "JPEG")
        self.source = output.getvalue()

    def list_albums(self):
        return [Album(id="old", name="Old"), Album(id="new", name="New")]

    def list_photos(self, album_id):
        self.calls.append(album_id)
        return [
            Photo(id=f"{album_id}-{index}", filename="photo.jpg", width=20, height=10)
            for index in range(2)
        ]

    def original(self, photo_id):
        if photo_id == "new-1":
            self.checking.set()
            assert self.release.wait(5)
            if self.fail:
                raise ProviderError("private server error")
        return self.source, "image/jpeg"

    def thumbnail(self, photo_id):
        return self.source, "image/jpeg"

    def validate_connection(self):
        return "Connected"


def configured_app(tmp_path: Path):
    provider = ControlledProvider()
    app = create_app(tmp_path, lambda _kind: provider)
    runtime = app.state.runtime
    runtime.albums = provider.list_albums()
    runtime.photos = provider.list_photos("old")
    runtime.loaded = True

    def configure(settings):
        settings.frame.album_id = "old"
        settings.frame.album_name = "Old"
        settings.refresh_status.next_attempt_at = datetime.now(UTC) + timedelta(days=1)

    runtime.repository.update(configure)
    runtime.photo_eligibility(runtime.repository.load().frame)
    runtime.renderer.last_rendered_photo_id = "old-0"
    return app, provider


def terminal_state(client):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        state = client.get("/api/activity").json()["album"]
        if not state["active"]:
            return state
        sleep(0.01)
    pytest.fail("album job did not finish")


def test_album_job_reports_real_progress_and_keeps_reads_responsive(tmp_path):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        try:
            accepted = client.post("/api/album/select", json={"album_id": "new"})
            assert accepted.status_code == 202
            assert provider.checking.wait(5)
            status = client.get("/api/activity").json()["album"]
            assert status["active"] and status["phase"] == "checking"
            assert (status["completed"], status["total"]) == (1, 2)
            assert runtime.repository.load().frame.album_id == "old"
            assert [p.id for p in runtime.catalog_snapshot()[1]] == ["old-0", "old-1"]
            assert client.get("/partials/workspace").status_code == 200
            assert client.get("/").status_code == 200
            assert (
                client.post("/api/album/select", json={"album_id": "new"}).json()["id"]
                == accepted.json()["id"]
            )
            assert client.post("/reset").status_code == 409
            assert client.post("/render/start").status_code == 409
            assert not runtime.refresh_lifecycle()
            assert not runtime.maintenance_gate.enter_and_drain(0)
        finally:
            provider.release.set()
        status = terminal_state(client)
        assert status["phase"] == "complete"
        assert (status["completed"], status["total"]) == (2, 2)
        assert runtime.repository.load().frame.album_id == "new"
        assert runtime.renderer.rendered_photo_id() == "old-0"
        assert runtime.preserved_display_photo().id == "old-0"
        assert runtime.maintenance_gate.enter_and_drain(0.2)
        runtime.maintenance_gate.cancel()


def test_failed_album_job_preserves_state_and_can_retry(tmp_path):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    runtime.set_preview("old-1")
    provider.fail = True
    provider.release.set()
    before = runtime.repository.load()
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        assert client.post("/api/album/select", json={"album_id": "new"}).status_code == 202
        failed = terminal_state(client)
        assert failed["phase"] == "failed"
        assert "unchanged" in failed["message"]
        assert "private server error" not in failed["message"]
        assert runtime.repository.load().frame == before.frame
        assert runtime.preview_id() == "old-1"
        assert [p.id for p in runtime.catalog_snapshot()[1]] == ["old-0", "old-1"]
        provider.fail = False
        assert client.post("/api/album/select", json={"album_id": "new"}).status_code == 202
        assert terminal_state(client)["phase"] == "complete"


def test_existing_mutation_excludes_album_job_atomically(tmp_path):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    with (
        runtime.interactive_operation(),
        TestClient(app, headers={"Origin": "http://testserver"}) as client,
    ):
        response = client.post("/api/album/select", json={"album_id": "new"})
        assert response.status_code == 409
        assert not app.state.album_change.snapshot()["active"]
    assert provider.calls == ["old"]


def test_album_job_waits_for_existing_refresh_before_publishing(tmp_path, monkeypatch):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    entered, release_refresh = Event(), Event()
    original = provider.list_photos

    def blocked_list(album_id):
        if album_id == "old":
            entered.set()
            assert release_refresh.wait(5)
        return original(album_id)

    monkeypatch.setattr(provider, "list_photos", blocked_list)
    runtime.repository.update(lambda saved: setattr(saved.refresh_status, "next_attempt_at", None))
    provider.release.set()
    # No app lifespan here: drive the refresh deterministically in this executor.
    client = TestClient(app, headers={"Origin": "http://testserver"})
    with ThreadPoolExecutor(max_workers=1) as executor:
        attempt = executor.submit(runtime.refresh_lifecycle)
        try:
            assert entered.wait(5)
            response = client.post("/api/album/select", json={"album_id": "new"})
            assert response.status_code == 202
            assert runtime.repository.load().frame.album_id == "old"
            assert "new" not in provider.calls
        finally:
            release_refresh.set()
        attempt.result(5)
        assert terminal_state(client)["phase"] == "complete"
    assert runtime.repository.load().frame.album_id == "new"
    assert [p.id for p in runtime.catalog_snapshot()[1]] == ["new-0", "new-1"]
    client.close()


def test_cancel_during_download_never_commits_and_releases_claim(tmp_path):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    runtime.set_preview("old-1")
    before = runtime.repository.load().frame
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        try:
            job = client.post("/api/album/select", json={"album_id": "new"}).json()
            assert provider.checking.wait(5)
            assert client.post("/api/album/cancel", json={"id": "stale"}).status_code == 409
            assert client.post("/api/album/cancel", json={"id": job["id"]}).status_code == 202
            state = client.get("/api/activity").json()["album"]
            assert state["phase"] == "cancelling" and state["active"]
            assert not state["cancellable"]
            assert runtime.repository.load().frame == before
        finally:
            provider.release.set()
        assert terminal_state(client)["phase"] == "cancelled"
        assert runtime.repository.load().frame == before
        assert runtime.preview_id() == "old-1"
        assert [photo.id for photo in runtime.catalog_snapshot()[1]] == ["old-0", "old-1"]
        assert client.post("/api/album/cancel", json={"id": job["id"]}).status_code == 409
        with runtime.interactive_operation():
            pass


def test_newest_album_replaces_pending_choice_without_publishing_cancelled_album(tmp_path):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    runtime.albums.extend([Album(id="middle", name="Middle"), Album(id="last", name="Last")])
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        try:
            first = client.post("/api/album/select", json={"album_id": "new"}).json()
            assert provider.checking.wait(5)
            middle = client.post("/api/album/select", json={"album_id": "middle"})
            assert middle.status_code == 202
            latest = client.post("/api/album/select", json={"album_id": "last"})
            assert latest.status_code == 202
            assert latest.json()["phase"] == "waiting"
            assert client.post("/api/album/cancel", json={"id": first["id"]}).status_code == 409
            assert runtime.repository.load().frame.album_id == "old"
        finally:
            provider.release.set()
        status = terminal_state(client)
        assert status["id"] == latest.json()["id"]
        assert status["phase"] == "complete"
        assert runtime.repository.load().frame.album_id == "last"
        assert "middle" not in provider.calls
        assert runtime.preserved_display_photo().id == "old-0"


def test_cancelled_startup_load_has_progress_keeps_picker_responsive_and_can_switch(
    tmp_path, monkeypatch
):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    runtime.clear_photos()
    runtime.cache.clear()
    started, release = Event(), Event()
    original = provider.original

    def blocked_original(photo_id):
        if photo_id == "old-1":
            started.set()
            assert release.wait(5)
        return original(photo_id)

    monkeypatch.setattr(provider, "original", blocked_original)
    provider.release.set()
    client = TestClient(app, headers={"Origin": "http://testserver"})
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(runtime.refresh_lifecycle)
        try:
            assert started.wait(5)
            state = client.get("/api/activity").json()["library"]
            assert state["active"] and state["phase"] == "checking"
            assert (state["completed"], state["total"]) == (1, 2)
            assert client.get("/partials/workspace").status_code == 200
            assert client.post("/api/album/select", json={"album_id": "new"}).status_code == 202
        finally:
            release.set()
        pending.result(5)
        assert terminal_state(client)["phase"] == "complete"
        assert runtime.library_load_snapshot()["phase"] == "cancelled"
        assert runtime.repository.load().frame.album_id == "new"
        assert all(photo.id.startswith("new-") for photo in runtime.catalog_snapshot()[1])
    client.close()


def test_manual_refresh_uses_counted_cancellable_job(tmp_path, monkeypatch):
    app, provider = configured_app(tmp_path)
    runtime = app.state.runtime
    original = provider.list_photos

    def larger_album(album_id):
        photos = original(album_id)
        return [*photos, Photo(id="new-1", filename="extra.jpg", width=20, height=10)]

    monkeypatch.setattr(provider, "list_photos", larger_album)
    before = runtime.repository.load().frame
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        try:
            response = client.post("/api/album/refresh")
            assert response.status_code == 202
            assert provider.checking.wait(5)
            state = client.get("/api/activity").json()["album"]
            assert (state["completed"], state["total"]) == (2, 3)
            assert (
                client.post("/api/album/cancel", json={"id": response.json()["id"]}).status_code
                == 202
            )
        finally:
            provider.release.set()
        assert terminal_state(client)["phase"] == "cancelled"
        assert runtime.repository.load().frame == before
        assert len(runtime.catalog_snapshot()[1]) == 2


def test_album_actions_reject_cross_origin_requests(tmp_path):
    app, _provider = configured_app(tmp_path)
    with TestClient(app) as client:
        for path in ("select", "refresh", "cancel"):
            response = client.post(
                f"/api/album/{path}",
                json={"album_id": "new", "id": "job"},
                headers={"Origin": "https://untrusted.example"},
            )
            assert response.status_code == 403
        assert not app.state.album_change.snapshot()["active"]


def test_cancellation_loses_to_commit_boundary_without_false_acceptance():
    from photoframe.services.album_load import AlbumLoad, AlbumLoadCancelled

    load = AlbumLoad("candidate", "Candidate")
    entered, release = Event(), Event()

    def commit():
        with load.committing():
            entered.set()
            assert release.wait(5)
        load.finish("complete", "Album ready")

    with ThreadPoolExecutor(max_workers=2) as executor:
        committing = executor.submit(commit)
        assert entered.wait(5)
        cancellation = executor.submit(load.cancel)
        release.set()
        committing.result(5)
        assert cancellation.result(5) is False
    assert load.snapshot()["phase"] == "complete"

    cancelled = AlbumLoad("candidate", "Candidate")
    assert cancelled.cancel()
    with pytest.raises(AlbumLoadCancelled), cancelled.committing():
        pytest.fail("A cancelled candidate must never reach publication")
