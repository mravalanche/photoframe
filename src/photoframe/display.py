"""Pimoroni Inky display discovery and rendering.

This module intentionally imports Inky only while a real display is requested.
That keeps the web application usable on development machines, and makes the
hardware boundary easy to substitute in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol

from PIL import Image

from .models import DeviceSettings, DisplayDriver

# These Impression drivers accept already indexed images without quantizing
# again. Their RGB path uses Pillow's private ImagingCore conversion API with
# an unloaded palette, which Pillow 13 correctly rejects as "empty palette".
# Other Inky families have different palette/remapping rules and retain their
# native set_image path.
_INDEXED_IMPRESSION_DRIVERS = frozenset({"inky.inky_uc8159", "inky.inky_ac073tc1a"})


class DisplayError(RuntimeError):
    """The configured display cannot be discovered or updated."""


@dataclass(frozen=True)
class DisplayProfile:
    driver: DisplayDriver
    model: str
    resolution: tuple[int, int]
    detected: bool
    status: str


class ImageDisplay(Protocol):
    """A panel which accepts a Pillow image at its native resolution."""

    @property
    def profile(self) -> DisplayProfile: ...

    def show(self, image: Image.Image) -> None: ...


def _manual_profile(settings: DeviceSettings, status: str) -> DisplayProfile | None:
    if not settings.display_size:
        return None
    mock = settings.display_driver == DisplayDriver.MOCK
    return DisplayProfile(
        driver=DisplayDriver.MOCK if mock else DisplayDriver.INKY,
        model=settings.display_model or ("Mock display" if mock else "Manually configured Inky"),
        resolution=settings.display_size,
        detected=False,
        status=status,
    )


class InkyDisplay:
    """Adapter for the official ``inky.auto.auto`` display discovery API."""

    def __init__(self, panel: Any, profile: DisplayProfile):
        self._panel = panel
        self._profile = profile

    @property
    def profile(self) -> DisplayProfile:
        return self._profile

    @classmethod
    def autodetect(cls) -> InkyDisplay:
        try:
            auto = import_module("inky.auto").auto
        except ImportError as exc:
            raise DisplayError(
                "Inky support is not installed. On the Pi, install the project's Inky extra."
            ) from exc
        try:
            panel = auto(ask_user=False, verbose=False)
            width, height = panel.resolution
        except Exception as exc:  # GPIO/I2C errors are hardware-specific.
            raise DisplayError(f"Could not auto-detect a Pimoroni Inky display: {exc}") from exc
        if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
            raise DisplayError("Inky returned an invalid display resolution")
        model = str(getattr(panel, "name", "") or type(panel).__name__)
        return cls(
            panel,
            DisplayProfile(
                driver=DisplayDriver.INKY,
                model=model,
                resolution=(width, height),
                detected=True,
                status=f"Detected {model} at {width} x {height}",
            ),
        )

    def show(self, image: Image.Image) -> None:
        if image.size != self.profile.resolution:
            raise DisplayError(
                f"Image is {image.size[0]} x {image.size[1]}; expected native "
                f"{self.profile.resolution[0]} x {self.profile.resolution[1]}"
            )
        indexed: Image.Image | None = None
        try:
            indexed = self._indexed_impression_image(image)
            self._panel.set_image(indexed if indexed is not None else image)
            # Inky's show() waits for the panel refresh. Do not emulate a busy
            # percentage: the call is the authoritative completion boundary.
            # The busy signal clearing is the only truthful completion boundary.
            # Refuse drivers which do not support this contract rather than
            # silently falling back to a non-blocking refresh.
            self._panel.show(busy_wait=True)
        except Exception as exc:
            raise DisplayError(f"Pimoroni Inky failed to refresh: {exc}") from exc
        finally:
            if indexed is not None:
                indexed.close()

    def _indexed_impression_image(self, image: Image.Image) -> Image.Image | None:
        if image.mode == "P" or not any(
            cls.__module__ in _INDEXED_IMPRESSION_DRIVERS for cls in type(self._panel).__mro__
        ):
            return None
        blend = getattr(self._panel, "_palette_blend", None)
        if not callable(blend):
            raise DisplayError("The Inky Impression driver does not expose its colour palette")
        # Use the driver's own calibrated palette and default saturation, not a
        # generic RGB palette. The pixel indices still have the panel's meaning.
        palette = blend(0.5)
        if (
            not isinstance(palette, (list, tuple, bytes, bytearray))
            or not 3 <= len(palette) <= 768
            or len(palette) % 3
            or any(not isinstance(value, int) or not 0 <= value <= 255 for value in palette)
        ):
            raise DisplayError("The Inky Impression driver returned an invalid colour palette")
        with Image.new("P", (1, 1)) as palette_image, image.convert("RGB") as rgb:
            palette_image.putpalette(palette)
            palette_image.load()
            return rgb.quantize(palette=palette_image, dither=Image.Dither.FLOYDSTEINBERG)


def discover_inky(settings: DeviceSettings) -> tuple[InkyDisplay | None, DisplayProfile]:
    """Discover the configured panel, falling back to a saved manual profile.

    The fallback retains an actionable profile for image preparation, but no
    driver object is returned: rendering must still fail safely until hardware
    can be reached.
    """
    if settings.display_driver == DisplayDriver.MOCK:
        profile = _manual_profile(settings, "Mock display selected") or DisplayProfile(
            DisplayDriver.MOCK, "Mock display", (800, 480), False, "Mock display selected"
        )
        return None, profile
    try:
        display = InkyDisplay.autodetect()
        return display, display.profile
    except DisplayError as exc:
        manual = _manual_profile(settings, f"Auto-detection unavailable: {exc}")
        if manual:
            return None, manual
        return None, DisplayProfile(
            DisplayDriver.INKY,
            settings.display_model or "Pimoroni Inky",
            (0, 0),
            False,
            str(exc),
        )


def apply_profile(settings: DeviceSettings, profile: DisplayProfile) -> None:
    """Persist detected (or manually selected) panel capabilities in TOML."""
    settings.display_driver = profile.driver
    settings.display_model = profile.model
    settings.display_detected = profile.detected
    settings.display_status = profile.status
    if profile.resolution != (0, 0):
        settings.set_display_size(*profile.resolution)
