"""The one SVG mark has browser and mobile home-screen companions."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from jinja2 import Environment, FileSystemLoader
from PIL import Image

STATIC = Path(__file__).parents[1] / "src" / "photoframe" / "static"


def test_inked_mark_is_a_self_contained_svg() -> None:
    icon = ElementTree.parse(STATIC / "photoframe.svg").getroot()
    assert icon.attrib["viewBox"] == "0 0 512 512"
    assert icon.attrib["id"] == "photoframe-inked-mark"
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
        assert (STATIC / urlsplit(icon["src"]).path.removeprefix("/static/")).is_file()
        assert icon["type"] == "image/png"
    assert {icon["purpose"] for icon in manifest["icons"]} == {"any", "maskable"}


def test_mobile_icons_are_opaque_and_maskable_mark_fits_safe_circle() -> None:
    for filename in ("apple-touch-icon.png", "icon-maskable-512.png"):
        with Image.open(STATIC / filename) as icon:
            assert icon.convert("RGBA").getchannel("A").getextrema() == (255, 255)
    with Image.open(STATIC / "icon-maskable-512.png") as icon:
        rgb = icon.convert("RGB")
        background = rgb.getpixel((0, 0))
        for y in range(rgb.height):
            for x in range(rgb.width):
                if rgb.getpixel((x, y)) != background:
                    assert (x - 256) ** 2 + (y - 256) ** 2 <= (512 * 0.4) ** 2


def test_shared_metadata_links_only_packaged_brand_assets() -> None:
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "link":
                self.links.append(dict(attrs))

    env = Environment(loader=FileSystemLoader(STATIC.parent / "templates"))
    html = env.from_string(
        '{% import "components/branding.html" as brand %}{{ brand.icons() }}'
    ).render()
    parser = Links()
    parser.feed(html)
    assert len({link["href"] for link in parser.links}) == len(parser.links)
    assert {link["type"] for link in parser.links if "type" in link} == {
        "image/png",
        "image/svg+xml",
    }
    assert {link["rel"] for link in parser.links} == {"icon", "apple-touch-icon", "manifest"}
    for link in parser.links:
        assert (STATIC / urlsplit(link["href"]).path.removeprefix("/static/")).is_file()
