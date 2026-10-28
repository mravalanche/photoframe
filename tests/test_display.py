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


class LegacyImpressionPanel(FakePanel):
    """The RGB/P handoff used by Inky 2.4's 7-colour Impression drivers."""

    __module__ = "inky.inky_ac073tc1a"
    colours: tuple[int, ...] = (
        0,
        0,
        0,
        255,
        255,
        255,
        0,
        255,
        0,
        0,
        0,
        255,
        255,
        0,
        0,
        255,
        255,
        0,
        255,
        140,
        0,
        255,
        255,
        255,
    )

    def _palette_blend(self, saturation):
        assert saturation == 0.5
        return list(self.colours)

    def set_image(self, image):
        if image.mode != "P":
            with Image.new("P", (1, 1)) as palette_image:
                palette_image.putpalette(self._palette_blend(0.5) + [0, 0, 0] * 248)
                image.load()
                # This exact upstream low-level conversion bypasses palette
                # loading and raises "empty palette" with patched Pillow 13.
                image.im.convert("P", True, palette_image.im)
        self.image = image.copy()


@pytest.mark.parametrize("module", ["inky.inky_ac073tc1a", "inky.inky_uc8159"])
def test_impression_refresh_uses_the_driver_palette_without_empty_palette(module):
    panel_type = type("Impression", (LegacyImpressionPanel,), {"__module__": module})
    panel = panel_type()
    display = InkyDisplay(panel, DisplayProfile(DisplayDriver.INKY, "Test", (800, 480), True, "ok"))
    with Image.new("RGB", (800, 480), "red") as image:
        display.show(image)
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert panel.shown and panel.busy_wait is True
    assert panel.image.mode == "P"
    assert panel.image.getpixel((0, 0)) == 4
    assert panel.image is not None
    palette = panel.image.getpalette()
    assert palette is not None
    assert palette[:24] == list(panel.colours)
    panel.image.close()


def test_existing_indexed_impression_image_is_not_requantized():
    panel = LegacyImpressionPanel()
    display = InkyDisplay(panel, DisplayProfile(DisplayDriver.INKY, "Test", (800, 480), True, "ok"))
    with Image.new("P", (800, 480), 5) as image:
        image.putpalette(panel.colours)
        display.show(image)
        assert image.getpixel((0, 0)) == 5
        assert panel.image.getpixel((0, 0)) == 5
    panel.image.close()


def test_impression_invalid_palette_fails_before_refresh():
    panel = LegacyImpressionPanel()
    panel.colours = ()
    display = InkyDisplay(panel, DisplayProfile(DisplayDriver.INKY, "Test", (800, 480), True, "ok"))
    with (
        Image.new("RGB", (800, 480)) as image,
        pytest.raises(DisplayError, match="invalid colour palette"),
    ):
        display.show(image)
    assert not panel.shown


def test_other_inky_families_keep_their_native_rgb_conversion():
    panel = FakePanel()
    display = InkyDisplay(panel, DisplayProfile(DisplayDriver.INKY, "Test", (800, 480), True, "ok"))
    with Image.new("RGB", (800, 480), "red") as image:
        display.show(image)
        assert panel.image is image


def test_real_refresh_error_is_not_hidden_by_palette_compatibility():
    class FailingImpression(LegacyImpressionPanel):
        def show(self, *, busy_wait):
            raise OSError("SPI device unavailable")

    panel = FailingImpression()
    display = InkyDisplay(panel, DisplayProfile(DisplayDriver.INKY, "Test", (800, 480), True, "ok"))
    with (
        Image.new("RGB", (800, 480)) as image,
        pytest.raises(DisplayError, match="SPI device unavailable"),
    ):
        display.show(image)
    panel.image.close()
