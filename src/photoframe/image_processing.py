"""Prepare source photographs for a frame's native e-ink panel size."""

from io import BytesIO
from typing import cast

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

MAX_DECODE_PIXELS = 16_000_000


class ImageProcessingError(ValueError):
    """A provider returned image data that cannot be prepared for the frame."""


def decode_image(source: bytes) -> Image.Image:
    """Decode source bytes through the same RGB path used by the renderer."""
    try:
        with Image.open(BytesIO(source)) as opened:
            if opened.width * opened.height > MAX_DECODE_PIXELS:
                # JPEG draft decoding downsamples in the codec, before Pillow
                # allocates the full camera-resolution image on a small Pi.
                factor = 2
                while (opened.width // factor) * (opened.height // factor) > MAX_DECODE_PIXELS:
                    factor *= 2
                opened.draft(
                    "RGB", (max(1, opened.width // factor), max(1, opened.height // factor))
                )
            if opened.width * opened.height > MAX_DECODE_PIXELS:
                raise ImageProcessingError("The photo is too large to decode safely")
            image = ImageOps.exif_transpose(opened)
            try:
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    with Image.new("RGBA", image.size, "white") as background:
                        with image.convert("RGBA") as foreground:
                            background.alpha_composite(foreground)
                        decoded = background.convert("RGB")
                else:
                    decoded = image.convert("RGB")
                decoded.load()
                return decoded
            finally:
                image.close()
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageProcessingError("The selected photo could not be decoded") from exc


def image_is_decodable(source: bytes) -> bool:
    """Return whether the installed Pillow pipeline can fully decode the asset."""
    try:
        decoded = decode_image(source)
    except ImageProcessingError:
        return False
    decoded.close()
    return True


def _photo_background(image: Image.Image, size: tuple[int, int], style: str) -> Image.Image:
    """Build quiet backdrops at thumbnail size without another source decode."""
    with image.resize((32, 32), Image.Resampling.BOX) as sample:
        # Outer regions avoid letting a face or a bright central subject dominate.
        pixels = [
            cast(tuple[int, int, int], sample.getpixel((x, y)))
            for y in range(32)
            for x in range(32)
            if x < 4 or x >= 28 or y < 4 or y >= 28
        ]
    channels = [sorted(pixel[channel] for pixel in pixels) for channel in range(3)]
    colour = tuple(channel[len(channel) // 2] for channel in channels)
    luminance = colour[0] * 0.2126 + colour[1] * 0.7152 + colour[2] * 0.0722
    muted = tuple(round(value * 0.35 + luminance * 0.65) for value in colour)
    if style == "colour":
        return Image.new("RGB", size, muted)

    scale = 96 / max(size)
    small_size = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
    with (
        ImageOps.fit(image, small_size, method=Image.Resampling.LANCZOS) as small,
        small.filter(ImageFilter.GaussianBlur(radius=12)) as blurred,
        ImageEnhance.Color(blurred).enhance(0.3) as desaturated,
        ImageEnhance.Contrast(desaturated).enhance(0.35) as softened,
        Image.new("RGB", small_size, muted) as wash,
        Image.blend(softened, wash, 0.3) as backdrop,
    ):
        return backdrop.resize(size, Image.Resampling.BICUBIC)


def prepare_for_display(
    source: bytes, target_size: tuple[int, int], *, fit_mode: str = "fill", matte: str = "white"
) -> Image.Image:
    """Return an RGB image that exactly fills ``target_size``.

    The image is oriented using EXIF metadata, cropped centrally only as needed
    to preserve its aspect ratio, and resized with Pillow's high-quality
    Lanczos resampling. RGB is deliberately used as the stable handoff format
    for future display drivers; panel-specific palette conversion belongs in
    those drivers.
    """
    width, height = target_size
    if width < 1 or height < 1:
        raise ValueError("Display dimensions must be positive")
    if fit_mode not in {"fit", "fill"} or matte not in {"black", "white", "blur", "colour"}:
        raise ValueError("Choose fit or fill and a supported photo background")
    image = decode_image(source)
    try:
        if fit_mode == "fit":
            contained = ImageOps.contain(image, target_size, method=Image.Resampling.LANCZOS)
            try:
                canvas = (
                    _photo_background(image, target_size, matte)
                    if matte in {"blur", "colour"}
                    else Image.new("RGB", target_size, matte)
                )
                canvas.paste(
                    contained, ((width - contained.width) // 2, (height - contained.height) // 2)
                )
                return canvas
            finally:
                contained.close()
        return ImageOps.fit(
            image,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    except (OSError, ValueError) as exc:
        raise ImageProcessingError("The selected photo could not be decoded") from exc
    finally:
        image.close()
