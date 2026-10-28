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
        assert client.get(url.replace("&amp;", "&")).status_code == 200
    assert len(revisions) == 1
