from io import BytesIO

import pytest
from PIL import Image

from photoframe.image_processing import ImageProcessingError, prepare_for_display
from photoframe.models import DeviceSettings


def jpeg(size: tuple[int, int], *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, "red")
    metadata = Image.Exif()
    if orientation:
        metadata[274] = orientation
    output = BytesIO()
    image.save(output, format="JPEG", exif=metadata)
    return output.getvalue()


def test_prepare_for_display_fills_native_size_and_converts_to_rgb():
    prepared = prepare_for_display(jpeg((1600, 1000)), (800, 480))

    assert prepared.size == (800, 480)
    assert prepared.mode == "RGB"


def test_prepare_for_display_applies_exif_orientation_before_fit():
    prepared = prepare_for_display(jpeg((1600, 1000), orientation=6), (480, 800))

    assert prepared.size == (480, 800)


def test_prepare_for_display_rejects_invalid_image_data():
    with pytest.raises(ImageProcessingError, match="could not be decoded"):
        prepare_for_display(b"not an image", (800, 480))


def test_display_size_requires_both_dimensions():
    with pytest.raises(ValueError, match="both display width"):
        DeviceSettings(display_width_px=800)

    assert DeviceSettings(display_width_px=800, display_height_px=480).display_size == (800, 480)
