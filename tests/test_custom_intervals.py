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


@pytest.mark.parametrize("mode", ["daily", "weekly", "interval"])
def test_disabled_inactive_schedule_fields_preserve_saved_choices(tmp_path, mode):
    app = create_app(tmp_path, demo_mode=True)
    repository = app.state.runtime.repository

    def configure(settings):
        settings.frame.rotation_seconds = 420
        settings.frame.daily_time = "10:23"
        settings.frame.weekly_day = 4
        settings.frame.weekly_time = "16:42"

    repository.update(configure)
    payload = workflow("7", "minutes")
    payload["schedule_mode"] = mode
    if mode != "interval":
        del payload["interval_value"]
        del payload["interval_unit"]
    if mode == "daily":
        payload["daily_time"] = "11:24"
    if mode == "weekly":
        payload["weekly_day"] = "2"
        payload["weekly_time"] = "17:43"
    response = TestClient(app).post("/workflow", data=payload)
    assert "Frame settings saved" in response.text
    saved = repository.load().frame
    assert saved.schedule_mode.value == mode
    assert saved.rotation_seconds == 420
    assert saved.daily_time == ("11:24" if mode == "daily" else "10:23")
    assert saved.weekly_day == (2 if mode == "weekly" else 4)
    assert saved.weekly_time == ("17:43" if mode == "weekly" else "16:42")


def test_missing_active_interval_never_uses_persisted_fallback(tmp_path):
    app = create_app(tmp_path, demo_mode=True)
    client = TestClient(app)
    payload = workflow("7", "minutes")
    del payload["interval_value"]
    del payload["interval_unit"]
    before = app.state.runtime.repository.load()
    result = client.post("/workflow", data=payload)
    assert "Enter a whole-number interval" in result.text
    assert app.state.runtime.repository.load() == before
