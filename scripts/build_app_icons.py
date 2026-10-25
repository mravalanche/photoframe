"""Build deterministic browser and home-screen icons from the Photoframe pixel mark."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "photoframe" / "static"

BACKGROUND = "#1c2721"
FRAME = "#a4d7b4"
PICTURE = "#d8e8d8"
HILLS = "#6c9b7c"

GRID_ORIGIN = 4
GRID_STEP = 7
PIXEL_SIZE = 5
CANVAS = 48

PIXELS: list[tuple[int, int, str]] = [
    *((row, col, FRAME) for row in range(6) for col in range(6) if row in {0, 5} or col in {0, 5}),
    (1, 4, PICTURE),  # A single quiet sun, distinct at favicon size.
    (2, 2, FRAME),
    (3, 2, HILLS),
    (3, 3, HILLS),
    (3, 4, HILLS),
    (4, 1, HILLS),
    (4, 2, HILLS),
    (4, 3, HILLS),
    (4, 4, HILLS),
]


def svg() -> str:
    pixels = "\n".join(
        f'    <rect x="{GRID_ORIGIN + col * GRID_STEP}" '
        f'y="{GRID_ORIGIN + row * GRID_STEP}" width="{PIXEL_SIZE}" '
        f'height="{PIXEL_SIZE}" fill="{colour}"/>'
        for row, col, colour in PIXELS
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" id="photoframe-pixel-mark" '
        'viewBox="0 0 48 48" role="img" aria-labelledby="photoframe-icon-title">\n'
        '  <title id="photoframe-icon-title">Photoframe</title>\n'
        f'  <rect width="48" height="48" rx="9" fill="{BACKGROUND}"/>\n'
        '  <g shape-rendering="crispEdges">\n'
        f"{pixels}\n"
        "  </g>\n"
        "</svg>\n"
    )


def render(size: int, *, maskable: bool = False) -> Image.Image:
    supersample = 4
    scale = size * supersample / CANVAS
    canvas = Image.new("RGB", (size * supersample, size * supersample), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    if not maskable:
        canvas = Image.new("RGBA", (size * supersample, size * supersample), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (0, 0, size * supersample - 1, size * supersample - 1),
            radius=9 * scale,
            fill=BACKGROUND,
        )
    else:
        # Android's mask may crop the outside 20%; keep the mark inside the safe zone.
        scale *= 0.62
    inset = (size * supersample - CANVAS * scale) / 2
    for row, col, colour in PIXELS:
        left = inset + (GRID_ORIGIN + col * GRID_STEP) * scale
        top = inset + (GRID_ORIGIN + row * GRID_STEP) * scale
        right = left + PIXEL_SIZE * scale
        bottom = top + PIXEL_SIZE * scale
        draw.rectangle((round(left), round(top), round(right - 1), round(bottom - 1)), fill=colour)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "photoframe.svg").write_text(svg())
    for size in (16, 32, 48):
        render(size).save(OUT / f"favicon-{size}.png", optimize=True)
    render(180).save(OUT / "apple-touch-icon.png", optimize=True)
    for size in (192, 512):
        render(size).save(OUT / f"icon-{size}.png", optimize=True)
    render(512, maskable=True).save(OUT / "icon-maskable-512.png", optimize=True)
    render(512).save(OUT / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])


if __name__ == "__main__":
    main()
