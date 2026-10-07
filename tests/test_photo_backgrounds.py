from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from photoframe.image_processing import prepare_for_display


@pytest.mark.parametrize("style", ["blur", "colour"])
@pytest.mark.parametrize("size", [(80, 120), (120, 80), (80, 80)])
def test_background_keeps_whole_foreground_unchanged(style, size):
    encoded = BytesIO()
    with Image.new("RGB", size, (40, 100, 70)) as source:
        draw = ImageDraw.Draw(source)
        draw.rectangle((20, 20, 60, 60), fill=(240, 30, 20))
        source.save(encoded, "PNG")
    target = (160, 100) if size[0] <= size[1] else (100, 160)
    with (
        prepare_for_display(encoded.getvalue(), target, fit_mode="fit", matte="white") as plain,
        prepare_for_display(encoded.getvalue(), target, fit_mode="fit", matte=style) as prepared,
    ):
        assert prepared.size == target and prepared.mode == "RGB"
        ratio = min(target[0] / size[0], target[1] / size[1])
        width, height = round(size[0] * ratio), round(size[1] * ratio)
        # Interior checks avoid Pillow's aspect-ratio rounding at the outermost pixel.
        x, y = (target[0] - width) // 2, (target[1] - height) // 2
        box = (x + 2, y + 2, x + width - 2, y + height - 2)
        with plain.crop(box) as expected, prepared.crop(box) as actual:
            assert actual.tobytes() == expected.tobytes()
        assert prepared.getpixel((0, 0)) != (255, 255, 255)
        if style == "colour":
            assert prepared.getpixel((0, 0)) == prepared.getpixel((target[0] - 1, target[1] - 1))


def test_fill_is_independent_of_background():
    encoded = BytesIO()
    with Image.new("RGB", (40, 80), "red") as source:
        source.save(encoded, "PNG")
    with (
        prepare_for_display(encoded.getvalue(), (120, 80), matte="white") as plain,
        prepare_for_display(encoded.getvalue(), (120, 80), matte="blur") as blurred,
    ):
        assert plain.tobytes() == blurred.tobytes()
