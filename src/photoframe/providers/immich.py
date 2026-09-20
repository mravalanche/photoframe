from datetime import UTC, datetime
from threading import BoundedSemaphore
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..models import Album, Photo
from .base import ProviderError


def _api_root(server_url: str) -> str:
    parts = urlsplit(server_url.strip().rstrip("/"))
    path = parts.path.rstrip("/")
    if not path.endswith("/api"):
        path += "/api"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class ImmichProvider:
    """Compatibility adapter for Immich's evolving, server-generated OpenAPI contract."""

    def __init__(self, server_url: str, api_key: str, client: httpx.Client | None = None):
        self.base_url = _api_root(server_url)
        self.client = client
        self._api_key = api_key

    MAX_RESPONSE_BYTES = 32 * 1024 * 1024
    MAX_ALBUM_PHOTOS = 20_000
    _request_slots = BoundedSemaphore(2)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self._request_slots.acquire(timeout=15):
            raise ProviderError("The photo source is busy; try again shortly")
        try:
            return self._bounded_request(method, path, **kwargs)
        finally:
            self._request_slots.release()

    def _bounded_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self.client or httpx.Client(
            base_url=self.base_url + "/",
            headers={"x-api-key": self._api_key, "accept": "application/json"},
            timeout=15,
            follow_redirects=True,
        )
        try:
            started = monotonic()
            with client.stream(method, path.lstrip("/"), **kwargs) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > self.MAX_RESPONSE_BYTES:
                        raise ProviderError(
                            "Immich response exceeds the frame's safe download limit"
                        )
                    if monotonic() - started > 60:
                        raise ProviderError("Immich download took too long; try again")
                    content.extend(chunk)
                return httpx.Response(
                    response.status_code,
                    headers={
                        key: value
                        for key, value in response.headers.items()
                        if key.lower() not in {"content-encoding", "content-length"}
                    },
                    content=bytes(content),
                    request=response.request,
                )
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Immich returned {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Immich: {exc}") from exc
        finally:
            if self.client is None:
                client.close()

    def validate_connection(self) -> str:
        albums = self.list_albums()
        return f"Connected to Immich; {len(albums)} album(s) visible"

    def list_albums(self) -> list[Album]:
        payload = self._request("GET", "/albums").json()
        if not isinstance(payload, list):
            raise ProviderError(
                "Immich album response was not a list; check server API compatibility"
            )
        return [
            Album(
                id=str(item["id"]),
                name=item.get("albumName") or item.get("name") or "Untitled album",
                asset_count=int(item.get("assetCount") or len(item.get("assets") or [])),
                thumbnail_asset_id=item.get("albumThumbnailAssetId"),
            )
            for item in payload
            if item.get("id")
        ]

    def list_photos(self, album_id: str) -> list[Photo]:
        album = self._request("GET", f"/albums/{album_id}").json()
        assets = album.get("assets") if isinstance(album, dict) else None
        if assets is None:
            assets = self._search_album(album_id)
        if not isinstance(assets, list):
            raise ProviderError(
                "Immich assets response was not a list; check server API compatibility"
            )
        if len(assets) > self.MAX_ALBUM_PHOTOS:
            raise ProviderError("This album exceeds the frame's safe photo limit")
        photos = [self._photo(item) for item in assets if item.get("type", "IMAGE") == "IMAGE"]
        earliest = datetime.min.replace(tzinfo=UTC)
        return sorted(photos, key=lambda p: ((p.taken_at or earliest).isoformat(), p.id))

    def _search_album(self, album_id: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request(
                "POST",
                "/search/metadata",
                json={"albumIds": [album_id], "type": "IMAGE", "page": page, "size": 1000},
            ).json()
            section = payload.get("assets", payload) if isinstance(payload, dict) else payload
            items = section.get("items", []) if isinstance(section, dict) else section
            if not isinstance(items, list):
                raise ProviderError("Immich metadata search shape is unsupported")
            found.extend(items)
            if len(found) > self.MAX_ALBUM_PHOTOS:
                raise ProviderError("This album exceeds the frame's safe photo limit")
            next_page = section.get("nextPage") if isinstance(section, dict) else None
            if not next_page or not items:
                break
            following_page = int(next_page)
            if following_page <= page:
                raise ProviderError("Immich metadata search repeated a page")
            page = following_page
        return found

    @staticmethod
    def _photo(item: dict[str, Any]) -> Photo:
        exif = item.get("exifInfo") or {}
        raw_date = item.get("localDateTime") or item.get("fileCreatedAt") or item.get("createdAt")
        try:
            taken = datetime.fromisoformat(raw_date) if raw_date else None
        except (ValueError, AttributeError):
            taken = None
        return Photo(
            id=str(item["id"]),
            filename=item.get("originalFileName") or item.get("filename") or str(item["id"]),
            width=exif.get("exifImageWidth") or item.get("width"),
            height=exif.get("exifImageHeight") or item.get("height"),
            taken_at=taken,
        )

    def thumbnail(self, photo_id: str) -> tuple[bytes, str]:
        response = self._request("GET", f"/assets/{photo_id}/thumbnail", params={"size": "preview"})
        return response.content, response.headers.get("content-type", "image/jpeg")

    def original(self, photo_id: str) -> tuple[bytes, str]:
        """Return the full-resolution image used for an e-ink refresh."""
        response = self._request("GET", f"/assets/{photo_id}/original")
        return response.content, response.headers.get("content-type", "image/jpeg")
