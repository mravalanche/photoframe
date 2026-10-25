"""The one SVG mark has browser and mobile home-screen companions."""

import json
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

STATIC = Path(__file__).parents[1] / "src" / "photoframe" / "static"


def test_pixel_mark_is_a_self_contained_svg() -> None:
    icon = ElementTree.parse(STATIC / "photoframe.svg").getroot()
    assert icon.attrib["viewBox"] == "0 0 48 48"
    assert icon.attrib["id"] == "photoframe-pixel-mark"
    assert len(icon.findall(".//{http://www.w3.org/2000/svg}rect")) >= 20
    assert "<script" not in (STATIC / "photoframe.svg").read_text()


def test_raster_icons_and_manifest_match_the_svg_family() -> None:
    sizes = {
        "favicon-16.png": 16,
        "favicon-32.png": 32,
        "favicon-48.png": 48,
        "apple-touch-icon.png": 180,
        "icon-192.png": 192,
        "icon-512.png": 512,
        "icon-maskable-512.png": 512,
    }
    for filename, size in sizes.items():
        with Image.open(STATIC / filename) as icon:
            assert icon.format == "PNG"
            assert icon.size == (size, size)
    with Image.open(STATIC / "favicon.ico") as icon:
        assert icon.format == "ICO"
        assert {(16, 16), (32, 32), (48, 48)} <= icon.info["sizes"]

    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    for icon in manifest["icons"]:
        assert (STATIC / icon["src"].removeprefix("/static/")).is_file()
        assert icon["type"] == "image/png"
    assert {icon["purpose"] for icon in manifest["icons"]} == {"any", "maskable"}
