"""Generate browser/mobile renditions of the approved, editable inked SVG.

Run after `npm ci`. Node/resvg is build tooling only; Pillow already ships with
the app. The served SVG is the source of truth, never a separate geometry copy.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "photoframe" / "static"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def palette() -> dict[str, str]:
    """Derive the three brand colours from the app's canonical theme tokens."""
    css = (ROOT / "assets/styles/v2.css").read_text()

    def token(theme: str, name: str) -> str:
        block = re.search(rf'name: "{theme}";([^}}]+)', css)
        if block is None:
            raise ValueError(f"Missing theme: {theme}")
        match = re.search(rf"--color-{name}: (#[0-9a-f]{{6}});", block[1])
        if match is None:
            raise ValueError(f"Missing theme token: {theme}/{name}")
        return match[1]

    background = token("photoframe-dark", "base-100")
    ink = token("photoframe-light", "base-100")
    middle = "#" + "".join(
        f"{round(int(ink[i : i + 2], 16) * 0.55 + int(background[i : i + 2], 16) * 0.45):02x}"
        for i in (1, 3, 5)
    )
    return {"bg": background, "ink": ink, "mid": middle}


def rendition(source: str, size: int, *, opaque: bool = False, maskable: bool = False) -> str:
    root = ET.fromstring(source)
    if size <= 32:
        groups = root.findall(f"{{{SVG_NS}}}g[@class='ink']")
        for group in groups:
            for rectangle in list(group):
                width = float(rectangle.get("width", "0"))
                if size == 16 or width < 16:
                    group.remove(rectangle)
        if size == 16:
            # Preserve the open left edge, sun and landscape; let each detached
            # ink square occupy one real pixel instead of a blurred fraction.
            for x, y in ((96, 192), (160, 160), (128, 256), (64, 288), (160, 320), (192, 352)):
                ET.SubElement(
                    groups[0],
                    f"{{{SVG_NS}}}rect",
                    {"x": str(x), "y": str(y), "width": "32", "height": "32"},
                )
            for path in root.findall(f"{{{SVG_NS}}}path[@fill='none']"):
                path.set("stroke-width", "32")
            circle = root.find(f".//{{{SVG_NS}}}circle")
            if circle is not None:
                circle.set("r", "30")
            landscape = root.find(f"{{{SVG_NS}}}g[@clip-path]")
            if landscape is not None:
                landscape.remove(list(landscape)[-1])

    background = root.find(f"{{{SVG_NS}}}rect[@class='bg']")
    if background is None:
        raise ValueError("The approved SVG must contain its background rectangle")
    if opaque or maskable:
        background.set("rx", "0")
    if maskable:
        # Full-bleed background with all artwork inside Android's safe circle.
        artwork = ET.Element(f"{{{SVG_NS}}}g", {"transform": "translate(38.4 38.4) scale(.85)"})
        for child in list(root):
            if child.tag in {f"{{{SVG_NS}}}g", f"{{{SVG_NS}}}path"}:
                root.remove(child)
                artwork.append(child)
        root.append(artwork)
    return "\n".join(line.rstrip() for line in ET.tostring(root, encoding="unicode").splitlines())


def main() -> None:
    colours = palette()
    canonical = OUT / "photoframe.svg"
    source = canonical.read_text()
    for name, value in colours.items():
        source = re.sub(rf"(--icon-{name}:\s*)[^;]+", rf"\g<1>{value}", source)
    canonical.write_text(source)
    (OUT / "favicon.svg").write_text(rendition(source, 16) + "\n")

    jobs = []
    for size, name, options in (
        (16, "favicon-16.png", {}),
        (32, "favicon-32.png", {}),
        (48, "favicon-48.png", {}),
        (180, "apple-touch-icon.png", {"opaque": True}),
        (192, "icon-192.png", {}),
        (512, "icon-512.png", {}),
        (512, "icon-maskable-512.png", {"maskable": True}),
    ):
        svg = rendition(source, size, **options)
        # Resolve custom properties for renderers without CSS var support.
        for colour, value in colours.items():
            svg = svg.replace(f"var(--icon-{colour})", value)
        jobs.append({"svg": svg, "size": size, "path": str(OUT / name)})
    subprocess.run(
        ["node", str(ROOT / "scripts/render_app_icons.mjs")],
        input=json.dumps(jobs),
        text=True,
        check=True,
    )
    with (
        Image.open(OUT / "favicon-16.png") as small,
        Image.open(OUT / "favicon-32.png") as medium,
        Image.open(OUT / "favicon-48.png") as large,
    ):
        large.save(
            OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=[small, medium]
        )


if __name__ == "__main__":
    main()
