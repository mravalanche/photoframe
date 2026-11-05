import re
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from photoframe.web import create_app
from photoframe.web.assets import ui_asset_revision


def test_same_version_script_edits_change_browser_asset_revision(tmp_path):
    (tmp_path / "v2.css").write_text("body{}")
    script = tmp_path / "activity.js"
    script.write_text("next")
    before = ui_asset_revision(tmp_path)
    script.write_text("hide")
    assert ui_asset_revision(tmp_path) != before


def test_page_assets_have_a_shared_nonempty_content_revision(tmp_path):
    client = TestClient(create_app(tmp_path, demo_mode=True))
    html = client.get("/").text
    urls = [
        url
        for url in re.findall(r'(?:src|href)="([^"]+\?v=[^"]+)"', html)
        if urlsplit(url).path.endswith((".css", ".js"))
    ]
    assert any("/activity.js?" in url for url in urls)
    assert any("/v2.css?" in url for url in urls)
    revisions = set()
    for url in urls:
        match = re.search(r"&amp;rev=([0-9a-f]{12})", url)
        assert match is not None, url
        revisions.add(match[1])
        assert not urlsplit(url).netloc, url
        assert client.get(url.replace("&amp;", "&")).status_code == 200
    assert len(revisions) == 1


def test_running_app_revisions_follow_rebuilt_assets(tmp_path, monkeypatch):
    from photoframe.web import routes

    assets = tmp_path / "assets"
    assets.mkdir()
    stylesheet = assets / "v2.css"
    stylesheet.write_text(".grid{display:grid}")
    (assets / "activity.js").write_text("initial")
    monkeypatch.setattr(routes, "ui_asset_revision", lambda _directory: ui_asset_revision(assets))
    client = TestClient(create_app(tmp_path / "data", demo_mode=True))
    before = client.get("/photos").text
    previous_revision = ui_asset_revision(assets)
    assert f"rev={previous_revision}" in before

    # Keep the process and package version unchanged, as during a live CSS rebuild.
    stylesheet.write_text(".grid{display:grid}.grid-cols-3{grid-template-columns:repeat(3,1fr)}")
    after = client.get("/photos").text
    assert f"rev={ui_asset_revision(assets)}" in after
    assert f"rev={previous_revision}" not in after


def test_static_urls_use_browser_origin_when_backend_host_differs(tmp_path):
    client = TestClient(create_app(tmp_path, demo_mode=True))
    html = client.get("/photos", headers={"Host": "internal-frame.invalid:8766"}).text
    static_urls = [url for url in re.findall(r'(?:src|href)="([^"]+)"', html) if "/static/" in url]
    assert static_urls
    assert all(url.startswith("/static/") for url in static_urls)
