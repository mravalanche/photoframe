from io import BytesIO

import pytest
from PIL import Image

from photoframe.cache import PhotoCache
from photoframe.image_processing import (
    ImageProcessingError,
    decode_image,
    image_is_decodable,
    prepare_for_display,
)
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


def test_decodability_uses_the_installed_image_pipeline():
    assert image_is_decodable(jpeg((1200, 800)))
    assert not image_is_decodable(b"HEIC-like bytes Pillow cannot decode")


def test_prepare_for_display_rejects_invalid_image_data():
    with pytest.raises(ImageProcessingError, match="could not be decoded"):
        prepare_for_display(b"not an image", (800, 480))


def test_display_size_requires_both_dimensions():
    with pytest.raises(ValueError, match="both display width"):
        DeviceSettings(display_width_px=800)

    assert DeviceSettings(display_width_px=800, display_height_px=480).display_size == (800, 480)


def test_large_jpeg_is_downsampled_before_allocating_full_decoded_image():
    with decode_image(jpeg((6000, 4000))) as decoded:
        assert decoded.width * decoded.height <= 16_000_000
        assert decoded.size == (3000, 2000)


def test_large_nondraft_image_is_rejected_before_pixel_decode(monkeypatch):
    monkeypatch.setattr("photoframe.image_processing.MAX_DECODE_PIXELS", 100)
    output = BytesIO()
    with Image.new("RGB", (20, 10), "red") as image:
        image.save(output, "PNG")
    assert not image_is_decodable(output.getvalue())


def test_previous_decode_verdict_is_invalidated_for_new_memory_limits(tmp_path):
    cache = PhotoCache(tmp_path, 1024)
    cache.set_decodability("photo", True)
    verdict = next(tmp_path.rglob("*.decodable"))
    verdict.write_text("supported-v2", encoding="ascii")
    assert cache.decodability("photo") is None


def test_oversized_legacy_cache_entry_is_not_read_into_memory(tmp_path, monkeypatch):
    cache = PhotoCache(tmp_path, 1024)
    cache.put("photo", b"x" * 101)

    def unexpected_read(_path):
        pytest.fail("Oversized original was read before checking its size")

    monkeypatch.setattr("pathlib.Path.read_bytes", unexpected_read)
    assert cache.get("photo", max_bytes=100) is None
