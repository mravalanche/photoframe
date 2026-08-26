from datetime import UTC, datetime, timedelta
from html import escape
from io import BytesIO
from typing import ClassVar

from PIL import Image

from ..models import Album, Photo


class DemoProvider:
    """Local screenshot/development provider. It never performs network I/O."""

    colours: ClassVar[dict[str, tuple[str, str, str]]] = {
        "coast": ("#25342f", "#d4a85f", "#758f7a"),
        "forest": ("#182521", "#9faf93", "#405d4d"),
        "dunes": ("#3b342c", "#d1b783", "#716750"),
        "lake": ("#202e32", "#b8c0b0", "#526f70"),
        "moor": ("#292826", "#c4865e", "#5e6651"),
        "portrait": ("#2c2724", "#c7a37b", "#6e5143"),
    }

    def __init__(self) -> None:
        base = datetime(2026, 8, 1, 12, tzinfo=UTC)
        self.photos = [
            Photo(
                id="coast",
                filename="Northumberland coast.jpg",
                width=1600,
                height=1000,
                taken_at=base,
            ),
            Photo(
                id="forest",
                filename="Forest path.jpg",
                width=1600,
                height=1000,
                taken_at=base + timedelta(days=1),
            ),
            Photo(
                id="dunes",
                filename="Evening dunes.jpg",
                width=1600,
                height=1000,
                taken_at=base + timedelta(days=2),
            ),
            Photo(
                id="lake",
                filename="Still lake.jpg",
                width=1600,
                height=1000,
                taken_at=base + timedelta(days=3),
            ),
            Photo(
                id="moor",
                filename="Moorland light.jpg",
                width=1600,
                height=1000,
                taken_at=base + timedelta(days=4),
            ),
            Photo(
                id="portrait",
                filename="Quiet portrait.jpg",
                width=1000,
                height=1600,
                taken_at=base + timedelta(days=5),
            ),
        ]

    def validate_connection(self) -> str:
        return "Local demo library ready"

    def list_albums(self) -> list[Album]:
        return [
            Album(
                id="demo-album",
                name="Quiet places",
                asset_count=len(self.photos),
                thumbnail_asset_id="coast",
            )
        ]

    def list_photos(self, album_id: str) -> list[Photo]:
        return self.photos if album_id == "demo-album" else []

    def thumbnail(self, photo_id: str) -> tuple[bytes, str]:
        sky, sun, land = self.colours.get(photo_id, self.colours["coast"])
        label = escape(
            next((photo.filename for photo in self.photos if photo.id == photo_id), "Photo")
        )
        portrait = photo_id == "portrait"
        width, height = (800, 1100) if portrait else (1200, 750)
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1200 750">
<rect width="1200" height="750" fill="{sky}"/><circle cx="900" cy="190" r="72" fill="{sun}" opacity=".88"/>
<path d="M0 520 Q250 310 520 520 T1200 430 V750 H0Z" fill="{land}"/>
<path d="M0 605 Q280 470 590 610 T1200 560 V750 H0Z" fill="#111816" opacity=".55"/>
<text x="52" y="690" fill="#f2efe6" opacity=".75" font-family="system-ui" font-size="26">{label}</text></svg>'''
        return svg.encode(), "image/svg+xml"

    def original(self, photo_id: str) -> tuple[bytes, str]:
        """Create a raster stand-in so the local demo exercises processing too."""
        sky, sun, land = self.colours.get(photo_id, self.colours["coast"])
        portrait = photo_id == "portrait"
        size = (1000, 1600) if portrait else (1600, 1000)
        image = Image.new("RGB", size, sky)
        # The simple banded scene is intentional: it keeps demo mode local and
        # provides enough variation to make cropping visible in development.
        image.paste(land, (0, int(size[1] * 0.6), size[0], size[1]))
        image.paste(
            sun, (int(size[0] * 0.7), int(size[1] * 0.12), int(size[0] * 0.8), int(size[1] * 0.22))
        )
        output = BytesIO()
        image.save(output, format="JPEG", quality=90)
        return output.getvalue(), "image/jpeg"
