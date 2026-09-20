from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photoframe.web import create_app


@pytest.mark.parametrize(
    ("seconds", "value", "unit"),
    [(91, 91, "seconds"), (420, 7, "minutes"), (5400, 90, "minutes"), (2592000, 30, "days")],
)
def test_interval_editor_preserves_exact_saved_duration(tmp_path: Path, seconds, value, unit):
    app = create_app(tmp_path, demo_mode=True)
    app.state.runtime.repository.update(
        lambda settings: setattr(settings.frame, "rotation_seconds", seconds)
    )
    with TestClient(app) as client:
        page = client.get("/partials/workspace").text
    assert f'value="{value}" aria-describedby="interval-help"' in page
    assert f'value="{unit}" selected' in page
    assert 'name="rotation_seconds"' not in page
    assert "30 seconds to 30 days" in page


@pytest.mark.parametrize(
    "seconds, index, label", [(30, 0, "30 seconds"), (3600, 5, "1 hour"), (86400, 9, "1 day")]
)
def test_interval_slider_selects_saved_detent(tmp_path: Path, seconds, index, label):
    app = create_app(tmp_path, demo_mode=True)
    app.state.runtime.repository.update(
        lambda settings: setattr(settings.frame, "rotation_seconds", seconds)
    )
    with TestClient(app) as client:
        page = client.get("/partials/workspace").text
    assert f'value="{index}" aria-valuetext="{label}"' in page
    assert "data-interval-custom checked" not in page
    assert "data-interval-custom-fields hidden" in page
    assert 'type="range" min="0" max="9" step="1"' in page
    assert 'list="interval-stops"' in page
    assert '<datalist id="interval-stops">' in page
    for stop in range(10):
        assert f'<option value="{stop}"></option>' in page


def test_nonpreset_interval_starts_custom_without_snapping(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    app.state.runtime.repository.update(
        lambda settings: setattr(settings.frame, "rotation_seconds", 420)
    )
    with TestClient(app) as client:
        page = client.get("/partials/workspace").text
    assert "data-interval-custom checked" in page
    assert "data-interval-slider disabled" in page
    assert "data-interval-custom-fields hidden" not in page
    assert 'for="interval-slider">7 minutes</output>' in page


def test_portrait_controls_and_last_hidden_photo_remain_recoverable(tmp_path: Path):
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        client.post("/album/select", data={"album_id": "demo-album"})
        runtime = app.state.runtime
        _albums, photos = runtime.catalog_snapshot()
        assert photos
        portrait = next(photo for photo in photos if photo.height > photo.width)
        page = client.post("/photo/preview", data={"photo_id": portrait.id}).text
        assert "Show whole photo" in page
        assert "Fill frame (crop edges)" in page
        assert "Save framing to include this photo" in page
        assert f"/photos/{portrait.id}/prepared?" in page
        assert "Never deletes the original" in page
        for photo in photos:
            page = client.post("/photo/hide", data={"photo_id": photo.id}).text
        assert "Undo hide" in page
        assert "Restore photo" in page
        assert "No visible photos are available" in page
        restored = client.post("/photo/unhide", data={"photo_id": portrait.id}).text
        assert f'aria-label="Preview {portrait.filename}"' in restored
