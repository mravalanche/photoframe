"""Prepare source photographs for a frame's native e-ink panel size."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

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


def prepare_for_display(source: bytes, target_size: tuple[int, int]) -> Image.Image:
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
    image = decode_image(source)
    try:
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
