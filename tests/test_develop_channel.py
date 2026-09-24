import io
import json
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_updater_manifest import manifest, signed

from photoframe.updater.helper import ReleaseSource, UpdateError, Updater
from photoframe.updater.managed import ManagedInstallation
from photoframe.updater.manifest import ManifestError, ReleaseManifest, semver
from photoframe.updater.protocol import Request
from photoframe.updater.state import StateStore, UpdaterState
from photoframe.web import create_app


def development(version="1.3.0.dev1"):
    raw = manifest()
    raw.update(version=version, bundle=f"photoframe-{version}-linux-aarch64.tar.gz")
    return raw


def test_version_order_and_signed_develop_identity():
    versions = ["1.2.1", "1.3.0.dev1", "1.3.0.dev2", "1.3.0.dev10", "1.3.0", "1.3.1.dev1"]
    assert sorted(reversed(versions), key=semver) == versions
    release = ReleaseManifest.parse(development())
    release.require_upgrade_from("1.2.1")
    with pytest.raises(ManifestError, match="not newer"):
        release.require_upgrade_from("1.3.0")
    ReleaseManifest.parse(manifest()).require_upgrade_from("1.3.0.dev10")
    assert UpdaterState.from_dict({"current_version": "1.3.0.dev1"}).current_version == "1.3.0.dev1"


@pytest.mark.parametrize("version", ["1.3.0.dev01", "1.3.0rc1", "1.3.0.dev1/evil", "1.3.0+local"])
def test_only_canonical_develop_versions_are_supported(version):
    with pytest.raises(ManifestError):
        semver(version)


def test_develop_requires_explicit_protocol_opt_in():
    value = dict(
        protocol=1, action="stage", token="test", request_id="a" * 32, release="1.3.0.dev1"
    )
    with pytest.raises(ValueError, match="channel"):
        Request.parse(json.dumps(value).encode(), "test")
    value["channel"] = "develop"
    assert Request.parse(json.dumps(value).encode(), "test").channel == "develop"
    value["release"] = "1.3.0"
    with pytest.raises(ValueError, match="channel"):
        Request.parse(json.dumps(value).encode(), "test")


def test_discovery_orders_versions_and_skips_unfinished_assets():
    private = Ed25519PrivateKey.generate()
    releases = [
        {"tag_name": "v1.3.0.dev9", "prerelease": True, "assets": []},
        {
            "tag_name": "v1.3.0.dev2",
            "prerelease": True,
            "assets": [{"name": "photoframe-manifest.json"}],
        },
        {
            "tag_name": "v1.3.0.dev1",
            "prerelease": True,
            "assets": [{"name": "photoframe-manifest.json"}],
        },
    ]
    opener = Mock(
        side_effect=[
            io.BytesIO(json.dumps(releases).encode()),
            io.BytesIO(signed(development("1.3.0.dev2"), private)),
        ]
    )
    source = ReleaseSource(private.public_key(), opener)
    assert source.latest("develop").manifest.version == "1.3.0.dev2"
    assert "/v1.3.0.dev2/photoframe-manifest.json" in opener.call_args.args[0]


def test_stable_discovery_rejects_even_validly_signed_develop_release():
    private = Ed25519PrivateKey.generate()
    source = ReleaseSource(
        private.public_key(), Mock(return_value=io.BytesIO(signed(development(), private)))
    )
    with pytest.raises(ManifestError, match="channel"):
        source.latest()


def test_offline_cache_cannot_cross_channels(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(UpdaterState(latest_manifest=manifest(), latest_channel="stable"))
    source = Mock()
    source.latest.side_effect = UpdateError("offline")
    updater = Updater(tmp_path, tmp_path / "data", store, source)
    assert updater.check("a" * 32, "develop")["ok"] is False
    assert store.load().latest_manifest is None
    store.save(UpdaterState(latest_manifest=development(), latest_channel="develop"))
    assert updater.check("b" * 32, "develop")["cached"] is True


def test_helper_rejects_cross_channel_stage_before_download(tmp_path):
    source = Mock()
    updater = Updater(tmp_path, tmp_path / "data", StateStore(tmp_path / "state.json"), source)
    with pytest.raises(ManifestError, match="channel"):
        updater.stage("a" * 32, "1.3.0.dev1")
    source.download.assert_not_called()
    source.latest.assert_not_called()


def test_channel_selection_is_persistent_authenticated_and_hides_other_channel(tmp_path):
    app = create_app(tmp_path)
    controller = app.state.updater
    controller.installation = ManagedInstallation(True, "Managed", tmp_path)
    controller.helper = Mock()
    controller.helper.call.return_value = {
        "phase": "available",
        "latest_manifest": manifest(),
        "latest_channel": "stable",
        "staged_version": "1.3.0",
    }
    client = TestClient(app)
    assert client.get("/api/updates/status").json()["channel"] == "stable"
    assert (
        client.post(
            "/api/updates/preferences", json={"weekly": True, "channel": "develop"}
        ).status_code
        == 403
    )
    session = client.post(
        "/api/updates/session", json={}, headers={"Origin": "http://testserver"}
    ).json()
    headers = {"Origin": "http://testserver", "X-CSRF-Token": session["csrf"]}
    response = client.post(
        "/api/updates/preferences", json={"weekly": True, "channel": "develop"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["latest_manifest"] is None
    assert response.json()["staged_version"] is None
    assert json.loads(controller.path.read_text())["channel"] == "develop"
    client.post("/api/updates/check", json={}, headers=headers)
    assert controller.helper.call.call_args.args[-1] == "develop"
    assert (
        client.post(
            "/api/updates/preferences", json={"weekly": True, "channel": {}}, headers=headers
        ).status_code
        == 400
    )
    controller.helper.call.return_value = {"phase": "staging"}
    assert (
        client.post(
            "/api/updates/preferences", json={"weekly": True, "channel": "stable"}, headers=headers
        ).status_code
        == 400
    )
