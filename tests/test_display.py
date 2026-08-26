from types import SimpleNamespace

import pytest
from PIL import Image

from photoframe.display import (
    DisplayError,
    DisplayProfile,
    InkyDisplay,
    apply_profile,
    discover_inky,
)
from photoframe.models import DeviceSettings, DisplayDriver


class FakePanel:
    resolution = (800, 480)
    name = "Inky Impression"

    def __init__(self):
        self.image = None
        self.shown = False
        self.busy_wait = None

    def set_image(self, image):
        self.image = image

    def show(self, *, busy_wait):
        self.shown = True
        self.busy_wait = busy_wait


def test_auto_detect_persists_native_capabilities(monkeypatch):
    panel = FakePanel()
    monkeypatch.setattr(
        "photoframe.display.import_module", lambda _: SimpleNamespace(auto=lambda **_: panel)
    )
    display = InkyDisplay.autodetect()
    settings = DeviceSettings()
    apply_profile(settings, display.profile)
    assert settings.display_driver == DisplayDriver.INKY
    assert settings.display_size == (800, 480)
    assert settings.display_detected is True
    assert "Inky Impression" in settings.display_status


def test_inky_requires_native_sized_image():
    panel = FakePanel()
    display = InkyDisplay(panel, DisplayProfile(DisplayDriver.INKY, "Test", (800, 480), True, "ok"))
    with pytest.raises(DisplayError, match="expected native"):
        display.show(Image.new("RGB", (100, 100)))
    display.show(Image.new("RGB", (800, 480)))
    assert panel.shown
    assert panel.busy_wait is True


def test_manual_dimensions_survive_missing_inky(monkeypatch):
    def unavailable(_):
        raise ImportError("no inky")

    monkeypatch.setattr("photoframe.display.import_module", unavailable)
    settings = DeviceSettings(display_width_px=600, display_height_px=448, display_model="Inky 5.7")
    display, profile = discover_inky(settings)
    assert display is None
    assert profile.resolution == (600, 448)
    assert profile.detected is False
