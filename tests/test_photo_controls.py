from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photoframe.image_processing import prepare_for_display
from photoframe.models import Album, AppSettings, Photo
from photoframe.web import create_app


class PhotoProvider:
    def validate_connection(self):
        return "Connected"

    def list_albums(self):
        return [Album(id="album", name="Test")]

    def list_photos(self, album_id):
        return [
            Photo(id="wide", filename="wide.jpg", width=120, height=80),
            Photo(id="tall", filename="tall.jpg", width=80, height=120),
        ]

    def original(self, photo_id):
        output = BytesIO()
        with Image.new("RGB", (80, 120) if photo_id == "tall" else (120, 80), "red") as image:
            image.save(output, "PNG")
        return output.getvalue(), "image/png"

    thumbnail = original


def frame(tmp_path):
    app = create_app(tmp_path, lambda _kind: PhotoProvider())
    runtime = app.state.runtime
    settings = runtime.repository.load()
    settings.provider.server_url = "https://photos.example"
    settings.verification.ok = True
    settings.frame.album_id = "album"
    settings.device.set_display_size(120, 80)
    runtime.repository.save(settings)
    runtime.refresh_albums()
    runtime.refresh_photos()
    return app, TestClient(app), runtime


@pytest.mark.parametrize("background", ["black", "blur", "colour"])
def test_portrait_is_browsable_but_rotation_requires_explicit_framing(tmp_path, background):
    _app, client, runtime = frame(tmp_path)
    assert [p.id for p in runtime.photo_eligibility(runtime.repository.load().frame).eligible] == [
        "wide"
    ]
    assert "tall.jpg" in client.get("/partials/workspace").text
    client.post("/photo/preview", data={"photo_id": "tall"})
    assert runtime.preview_id() == "tall"
    assert "not eligible" in client.post("/render/start").text
    response = client.post(
        "/photo/framing", data={"photo_id": "tall", "fit_mode": "fit", "matte": background}
    )
    assert "Framing saved" in response.text
    assert {p.id for p in runtime.photo_eligibility(runtime.repository.load().frame).eligible} == {
        "wide",
        "tall",
    }
    assert runtime.renderer.rendered_photo_id() is None
    with (
        Image.open(BytesIO(client.get("/photos/tall/prepared").content)) as preview,
        runtime.prepare_photo("tall") as hardware,
    ):
        assert preview.tobytes() == hardware.tobytes()
        if background == "black":
            assert preview.getpixel((0, 0)) == (0, 0, 0)
        else:
            assert preview.getpixel((0, 0)) != (255, 0, 0)
        assert preview.getpixel((60, 40)) == (255, 0, 0)
    assert runtime.repository.load().frame.preference("tall").matte == background


def test_unsaved_preview_does_not_modify_framing_or_enable_rotation(tmp_path):
    _app, client, runtime = frame(tmp_path)
    response = client.get("/photos/tall/prepared?fit_mode=fit&matte=black")
    assert response.status_code == 200
    assert not runtime.repository.load().frame.preference("tall").included
    assert client.get("/photos/tall/prepared?fit_mode=invalid").status_code == 422
    assert (
        client.post("/photo/framing", data={"photo_id": "tall", "fit_mode": "invalid"}).status_code
        == 200
    )
    assert not runtime.repository.load().frame.preference("tall").included


def test_hide_last_photo_is_local_restorable_and_blocks_stale_actions(tmp_path):
    _app, client, runtime = frame(tmp_path)
    runtime.repository.update(
        lambda saved: setattr(saved.refresh_status, "last_rendered_photo_id", "wide")
    )
    client.post("/photo/preview", data={"photo_id": "wide"})
    response = client.post("/photo/hide", data={"photo_id": "wide"})
    assert "No photos remain in rotation" in response.text
    assert runtime.repository.load().refresh_status.last_rendered_photo_id == "wide"
    assert runtime.preview_id() is None
    assert runtime.photo_eligibility(runtime.repository.load().frame).eligible == []
    assert "hidden" in client.post("/photo/preview", data={"photo_id": "wide"}).text
    assert "not eligible" in client.post("/photo/start", data={"photo_id": "wide"}).text
    assert client.get("/photos/wide/prepared").status_code == 422
    assert "Photo restored" in client.post("/photo/unhide", data={"photo_id": "wide"}).text
    assert [p.id for p in runtime.photo_eligibility(runtime.repository.load().frame).eligible] == [
        "wide"
    ]


def test_preferences_persist_and_are_scoped_to_source(tmp_path):
    _app, client, runtime = frame(tmp_path)
    client.post("/photo/framing", data={"photo_id": "tall", "fit_mode": "fit", "matte": "black"})
    client.post("/photo/hide", data={"photo_id": "tall"})
    original_key = runtime._cache_key("tall")
    runtime.repository.update(
        lambda saved: setattr(saved.provider, "server_url", "https://other.example")
    )
    assert not runtime.repository.load().frame.preference("tall").hidden
    assert not runtime.repository.load().frame.preference("tall").included
    assert runtime._cache_key("tall") != original_key
    runtime.repository.update(
        lambda saved: setattr(saved.provider, "server_url", "https://photos.example")
    )
    saved = runtime.repository.load().frame.preference("tall")
    assert saved.hidden and saved.included and saved.fit_mode == "fit" and saved.matte == "black"
    assert AppSettings().frame.photo_preferences == {}


def test_fit_applies_exif_before_layout_and_preserves_white_matte():
    output = BytesIO()
    with Image.new("RGB", (120, 80), "red") as image:
        exif = Image.Exif()
        exif[274] = 6
        image.save(output, "JPEG", exif=exif)
    with prepare_for_display(
        output.getvalue(), (120, 80), fit_mode="fit", matte="white"
    ) as prepared:
        assert prepared.size == (120, 80)
        assert prepared.getpixel((0, 40)) == (255, 255, 255)
        center = prepared.getpixel((60, 40))
        assert isinstance(center, tuple) and center[0] > 240


def test_edits_are_rejected_during_render_and_album_change(tmp_path):
    _app, client, runtime = frame(tmp_path)
    runtime.renderer.start("wide")
    assert (
        "Wait for the current frame update"
        in client.post("/photo/hide", data={"photo_id": "wide"}).text
    )
    assert not runtime.repository.load().frame.preference("wide").hidden
    runtime.renderer.reset()
    runtime.claim_album_change()
    try:
        assert client.post("/photo/hide", data={"photo_id": "wide"}).status_code == 409
    finally:
        runtime.release_album_change()


def test_hidden_photos_are_not_counted_as_wrong_orientation(tmp_path):
    _app, client, runtime = frame(tmp_path)
    client.post("/photo/hide", data={"photo_id": "tall"})
    assert runtime.photo_eligibility(runtime.repository.load().frame).wrong_orientation == 0


def test_failed_source_switch_cannot_store_old_photo_preferences(tmp_path):
    _app, client, runtime = frame(tmp_path)

    class BrokenProvider(PhotoProvider):
        def validate_connection(self):
            raise RuntimeError("Offline")

    runtime.provider_resolver = lambda _kind: BrokenProvider()
    client.post(
        "/connection",
        data={"server_url": "https://other.example", "api_key": "new"},  # pragma: allowlist secret
    )
    assert runtime.catalog_snapshot()[1] == []
    client.post("/photo/hide", data={"photo_id": "wide"})
    assert runtime.repository.load().frame.photo_preferences == {}


def test_successful_display_snapshot_survives_drafts_failure_hide_and_restart(tmp_path):
    _app, client, runtime = frame(tmp_path)
    client.post("/photo/framing", data={"photo_id": "tall", "fit_mode": "fit", "matte": "black"})
    client.post("/photo/preview", data={"photo_id": "tall"})
    client.post("/render/start")
    from datetime import timedelta

    started = runtime.renderer.snapshot().started_at
    assert started is not None
    runtime.renderer.update(runtime.repository.load().device, started + timedelta(seconds=30))
    original = client.get("/photos/displayed").content
    snapshot = runtime.displayed_snapshot_photo()
    assert snapshot is not None and snapshot.id == "tall"
    client.post("/photo/framing", data={"photo_id": "tall", "fit_mode": "fill", "matte": "white"})
    client.get("/photos/tall/prepared?fit_mode=fit&matte=white")
    client.post("/photo/preview", data={"photo_id": "wide"})
    client.post("/render/start")
    failed_start = runtime.renderer.snapshot().started_at
    assert failed_start is not None
    failed = runtime.renderer.update(
        runtime.repository.load().device, failed_start + timedelta(seconds=90)
    )
    assert failed.phase.value == "failed"
    assert client.get("/photos/displayed").content == original
    client.post("/photo/hide", data={"photo_id": "tall"})
    assert client.get("/photos/displayed").content == original
    restarted = create_app(tmp_path, lambda _kind: PhotoProvider())
    assert TestClient(restarted).get("/photos/displayed").content == original
    assert "/photos/displayed" in client.get("/partials/frame-status").text


def test_preparation_serializes_large_decodes(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _app, _client, runtime = frame(tmp_path)
    entered, release = Event(), Event()
    calls = []
    original = runtime.render_source

    def blocked(photo_id):
        calls.append(photo_id)
        entered.set()
        assert release.wait(5)
        return original(photo_id)

    monkeypatch.setattr(runtime, "render_source", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runtime.prepare_photo, "wide", preview=True)
        assert entered.wait(5)
        second = executor.submit(runtime.prepare_photo, "tall", preview=True)
        assert calls == ["wide"]
        release.set()
        first.result().close()
        second.result().close()
    assert calls == ["wide", "tall"]


def test_snapshot_write_failure_does_not_undo_successful_render(tmp_path, monkeypatch):
    from datetime import timedelta

    _app, client, runtime = frame(tmp_path)
    client.post("/photo/preview", data={"photo_id": "wide"})
    client.post("/render/start")

    def no_space(*args, **kwargs):
        raise OSError("Disk full")

    monkeypatch.setattr("photoframe.services.runtime.atomic_write", no_space)
    started = runtime.renderer.snapshot().started_at
    assert started is not None
    state = runtime.renderer.update(
        runtime.repository.load().device, started + timedelta(seconds=30)
    )
    assert state.phase.value == "complete"
    assert runtime.repository.load().refresh_status.last_rendered_photo_id == "wide"
    assert runtime.displayed_snapshot_photo() is None
    assert client.get("/photos/displayed").status_code == 404
