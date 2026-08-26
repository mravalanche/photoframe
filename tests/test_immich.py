import httpx

from photoframe.providers.immich import ImmichProvider


def test_legacy_album_assets_are_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/api/")
        if request.url.path.endswith("/albums"):
            return httpx.Response(200, json=[{"id": "album", "albumName": "Trip", "assetCount": 1}])
        return httpx.Response(
            200,
            json={
                "assets": [
                    {
                        "id": "photo",
                        "type": "IMAGE",
                        "originalFileName": "one.jpg",
                        "exifInfo": {"exifImageWidth": 1200, "exifImageHeight": 800},
                    }
                ]
            },
        )

    client = httpx.Client(
        base_url="https://immich.test/api/", transport=httpx.MockTransport(handler)
    )
    provider = ImmichProvider("https://immich.test", "key", client)
    assert provider.validate_connection().startswith("Connected")
    assert provider.list_photos("album")[0].width == 1200


def test_new_album_shape_uses_metadata_search():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": "album", "albumName": "Trip"})
        assert request.url.path == "/api/search/metadata"
        return httpx.Response(
            200,
            json={
                "assets": {
                    "items": [
                        {
                            "id": "photo",
                            "type": "IMAGE",
                            "originalFileName": "new.jpg",
                            "width": 800,
                            "height": 1200,
                        }
                    ],
                    "nextPage": None,
                }
            },
        )

    client = httpx.Client(
        base_url="https://immich.test/api/", transport=httpx.MockTransport(handler)
    )
    photos = ImmichProvider("https://immich.test", "key", client).list_photos("album")
    assert [(p.filename, p.height) for p in photos] == [("new.jpg", 1200)]
