"""Gallery captions and current-frame state use saved domain data, not draft previews."""

import re
from html import unescape

from fastapi.testclient import TestClient

from photoframe.web import create_app

ORIGIN = {"Origin": "http://testserver", "X-Photoframe-View": "photos"}


def tiles(html: str) -> dict[str, str]:
    return dict(
        re.findall(r'<form\b[^>]*data-gallery-photo="([^"]+)"[^>]*>(.*?)</form>', html, re.S)
    )


def test_gallery_uses_effective_saved_framing_and_preserves_full_filenames(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    filename = 'Summer & family <2026> "favourite" ' + "long-name-" * 20 + ".jpg"
    runtime.photos[0].filename = filename
    client = TestClient(app, headers=ORIGIN)
    client.post("/photo/default-layout", data={"fit_mode": "fit", "matte": "blur"})
    client.post(
        "/photo/framing", data={"photo_id": "portrait", "fit_mode": "fill", "matte": "white"}
    )
    response = client.get("/partials/workspace?view=photos")
    gallery = tiles(response.text)
    assert len(gallery) == len(runtime.photos)
    coast = gallery["coast"]
    assert 'aria-haspopup="dialog"' in coast
    assert 'aria-controls="photo-editor-dialog"' not in coast
    assert 'hx-target="#photo-workspace"' in response.text
    assert 'hx-select-oob="#workspace-notices"' in response.text
    assert "Default framing · Whole photo · Soft photo" in coast
    assert filename in unescape(coast)
    assert "<2026>" not in coast
    filename_title = re.search(r'title="([^"]+)" data-gallery-filename', coast)
    assert filename_title and unescape(filename_title[1]) == filename
    assert 'loading="lazy" alt=""' in coast
    assert "truncate pf-metadata" in coast
    assert "Custom framing · Fill frame" in gallery["portrait"]
    button_body = re.search(r"<button[^>]*>(.*?)</button>", coast, re.S)
    assert button_body and filename not in unescape(button_body[1])
    assert "data-gallery-caption" in coast.split("</button>", 1)[1]
    assert 'id="photo-layouts"' in response.text
    assert 'id="all-photo-layouts"' in response.text


def test_gallery_on_frame_badge_follows_confirmed_display_not_preview(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    client = TestClient(app, headers=ORIGIN)
    assert "data-on-frame" not in client.get("/partials/workspace?view=photos").text
    runtime.repository.update(
        lambda settings: setattr(settings.refresh_status, "last_rendered_photo_id", "coast")
    )
    response = client.post("/photo/preview", data={"photo_id": "portrait"})
    gallery = tiles(response.text)
    assert "data-on-frame" in gallery["coast"]
    assert "badge-info" in gallery["coast"]
    assert "On frame now" in gallery["coast"]
    assert "data-on-frame" not in gallery["portrait"]
    assert 'data-editor-open="true"' in response.text
    assert 'aria-controls="photo-editor-dialog"' in gallery["portrait"]
    assert response.text.count("data-on-frame") == 1
    response = client.post(
        "/photo/framing", data={"photo_id": "portrait", "fit_mode": "fit", "matte": "black"}
    )
    assert "Custom framing · Whole photo · Black" in tiles(response.text)["portrait"]
    assert "data-on-frame" in tiles(response.text)["coast"]


def test_gallery_hidden_photos_remain_restorable_and_bulk_layout_still_applies(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app, headers=ORIGIN)
    hidden = client.post("/photo/hide", data={"photo_id": "portrait"})
    assert "portrait" not in tiles(hidden.text)
    assert "Hidden from this frame (1)" in hidden.text
    assert "Restore" in hidden.text and "Undo" in hidden.text
    restored = client.post("/photo/unhide", data={"photo_id": "portrait"})
    assert "portrait" in tiles(restored.text)
    bulk = client.post("/photo/layouts/all", data={"fit_mode": "fit", "matte": "white"})
    assert all(
        "Default framing · Whole photo · White" in tile for tile in tiles(bulk.text).values()
    )
    frame = app.state.runtime.repository.load().frame
    assert all(
        not framing.included
        for framing in frame.photo_preferences.get(frame.photo_scope, {}).values()
    )
