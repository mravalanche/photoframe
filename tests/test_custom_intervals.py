"""Custom interval durations use the same contract for saving and previewing."""

import pytest
from fastapi.testclient import TestClient

from photoframe.web.forms import parse_rotation_seconds
from photoframe.web.routes import create_app


@pytest.mark.parametrize(
    ("value", "unit", "seconds"),
    [
        ("30", "seconds", 30),
        ("90", "seconds", 90),
        ("7", "minutes", 420),
        ("90", "minutes", 5400),
        ("7", "hours", 25200),
        ("30", "days", 2592000),
    ],
)
def test_custom_interval_exact_conversion(value, unit, seconds):
    assert parse_rotation_seconds({"interval_value": value, "interval_unit": unit}) == seconds


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("29", "seconds"),
        ("0", "minutes"),
        ("-1", "hours"),
        ("31", "days"),
        ("1.5", "hours"),
        ("NaN", "seconds"),
        ("Infinity", "days"),
        ("", "minutes"),
        ("1e3", "seconds"),
        ("99999999999999", "seconds"),
        ("2", "weeks"),
    ],
)
def test_invalid_intervals_never_round_or_fall_back_to_old_seconds(value, unit):
    with pytest.raises(ValueError):
        parse_rotation_seconds(
            {"interval_value": value, "interval_unit": unit, "rotation_seconds": "3600"}
        )


def test_legacy_seconds_are_preserved():
    assert parse_rotation_seconds({"rotation_seconds": "91"}) == 91
    assert parse_rotation_seconds({}, default=91) == 91


def workflow(value, unit):
    return {
        "orientation": "landscape",
        "schedule_mode": "interval",
        "interval_value": value,
        "interval_unit": unit,
        "photo_order": "album",
        "expected_refresh_seconds": "8",
        "render_timeout_seconds": "90",
        "display_driver": "mock",
        "display_width_px": "1200",
        "display_height_px": "750",
    }


def test_custom_interval_preview_save_and_invalid_save_are_consistent(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    assert client.post("/schedule/preview", data=workflow("7", "minutes")).status_code == 200
    result = client.post("/workflow", data=workflow("7", "minutes"))
    assert result.status_code == 200
    assert app.state.runtime.repository.load().frame.rotation_seconds == 420
    assert "Every 7 minutes" in result.text
    for value, unit in [("29", "seconds"), ("1.5", "hours"), ("31", "days")]:
        before = app.state.runtime.repository.load()
        assert client.post("/schedule/preview", data=workflow(value, unit)).status_code == 422
        client.post("/workflow", data=workflow(value, unit))
        assert app.state.runtime.repository.load() == before
