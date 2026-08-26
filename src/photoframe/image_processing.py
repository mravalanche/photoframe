"""Prepare source photographs for a frame's native e-ink panel size."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageProcessingError(ValueError):
    """A provider returned image data that cannot be prepared for the frame."""


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
    try:
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                background = Image.new("RGBA", image.size, "white")
                background.alpha_composite(image.convert("RGBA"))
                image = background.convert("RGB")
            else:
                image = image.convert("RGB")
            return ImageOps.fit(
                image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageProcessingError("The selected photo could not be decoded") from exc
