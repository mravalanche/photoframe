"""Exact release selection remains a helper-authenticated, forward-only operation."""

import base64
import io
import json
from typing import cast
from unittest.mock import Mock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from photoframe.updater.helper import ReleaseSource, UpdateError, Updater
from photoframe.updater.managed import ManagedInstallation
from photoframe.updater.manifest import (
    ManifestError,
    ReleaseManifest,
    SignedManifest,
    canonical_json,
)
from photoframe.updater.protocol import SELECTED_RELEASES_CAPABILITY, ProtocolError, Request
from photoframe.updater.state import StateStore, UpdaterState
from photoframe.web import create_app


def manifest(version="9.0.0", **changes):
    result = {
        "version": version,
        "commit": "a" * 40,
        "repository": "mravalanche/photoframe",
        "platform": "linux-aarch64",
        "bundle": f"photoframe-{version}-linux-aarch64.tar.gz",
        "bundle_size": 1,
        "bundle_sha256": "a" * 64,
        "updater_protocol": 1,
        "settings_schema_min": 2,
        "settings_schema_max": 2,
        "rollback_schema_min": 2,
        "rollback_schema_max": 2,
        "release_notes": "Verified release notes",
        "published_at": "2026-10-25T12:00:00Z",
    }
    return {**result, **changes}


def metadata(version="9.0.0", **changes):
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": ".dev" in version,
        "published_at": "2026-10-25T12:00:00Z",
        "assets": [
            {"name": "photoframe-manifest.json"},
            {"name": f"photoframe-{version}-linux-aarch64.tar.gz"},
        ],
        **changes,
    }


def authenticated_source(raw=None, release_metadata=None, wrong_key=False):
    private = Ed25519PrivateKey.generate()
    signer = Ed25519PrivateKey.generate() if wrong_key else private
    raw = raw or manifest()
    envelope = json.dumps(
        {"manifest": raw, "signature": base64.b64encode(signer.sign(canonical_json(raw))).decode()}
    ).encode()
    calls = []

    def open_url(url, *, timeout):
        calls.append(url)
        return io.BytesIO(
            json.dumps(release_metadata or metadata()).encode()
            if "/releases/tags/" in url
            else envelope
        )

    return ReleaseSource(private.public_key(), open_url), calls


def helper(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(UpdaterState(current_version="8.0.0"))
    source = Mock()
    source.release.return_value = SignedManifest(ReleaseManifest.parse(manifest()), b"")
    return Updater(tmp_path, tmp_path / "data", store, source, command=Mock())


def test_exact_source_uses_only_fixed_tag_urls_and_authenticates_manifest():
    source, calls = authenticated_source()
    assert source.release("9.0.0", "stable").manifest.version == "9.0.0"
    assert calls == [
        "https://api.github.com/repos/mravalanche/photoframe/releases/tags/v9.0.0",
        "https://github.com/mravalanche/photoframe/releases/download/v9.0.0/photoframe-manifest.json",
    ]
    with pytest.raises(ManifestError):
        source.release("https://example.test/manifest", "stable")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"draft": True},
        {"tag_name": "v10.0.0"},
        {"prerelease": True},
        {"assets": []},
    ],
)
def test_exact_source_rejects_unpublished_wrong_tag_channel_or_missing_bundle(changes):
    source, calls = authenticated_source(release_metadata=metadata(**changes))
    with pytest.raises(UpdateError):
        source.release("9.0.0", "stable")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"platform": "linux-x86_64"},
        {"updater_protocol": 2},
        {"settings_schema_min": 3},
    ],
)
def test_exact_source_rejects_incompatible_signed_manifest(changes):
    source, _ = authenticated_source(raw=manifest(**changes))
    with pytest.raises(ManifestError):
        source.release("9.0.0", "stable")


def test_exact_source_rejects_wrong_signature_and_signed_version():
    source, _ = authenticated_source(wrong_key=True)
    with pytest.raises(ManifestError, match="signature"):
        source.release("9.0.0", "stable")
    source, _ = authenticated_source(raw=manifest("10.0.0"))
    with pytest.raises(UpdateError, match="selected tag"):
        source.release("9.0.0", "stable")


def test_helper_inspection_is_read_only_even_while_another_operation_is_active(tmp_path):
    service = helper(tmp_path)
    state = service.state_store.load()
    state.phase = "activating"
    service.state_store.save(state)
    before = service.state_store.path.read_bytes()
    result = service.dispatch(Request(1, "inspect", "x", "a" * 32, "9.0.0"))
    assert result["manifest"]["version"] == "9.0.0"
    assert service.state_store.path.read_bytes() == before
    cast(Mock, service.source).download.assert_not_called()


def test_selected_stage_reauthenticates_and_keeps_latest_stage_policy(tmp_path):
    service = helper(tmp_path)
    slot = service.versions / "9.0.0"
    slot.mkdir(parents=True)
    (slot / ".ready").write_text("ready")
    service.inspect("9.0.0", "stable")
    assert service.stage("a" * 32, "9.0.0", selected=True)["phase"] == "staged"
    assert cast(Mock, service.source).release.call_count == 2
    cast(Mock, service.source).latest.assert_not_called()
    cast(Mock, service.source).latest.return_value = SignedManifest(
        ReleaseManifest.parse(manifest("10.0.0")), b""
    )
    with pytest.raises(UpdateError, match="authenticated latest"):
        service.stage("b" * 32, "9.0.0")


@pytest.mark.parametrize("current", [None, "9.0.0", "10.0.0"])
def test_selected_stage_rejects_unknown_equal_or_newer_running_version(tmp_path, current):
    service = helper(tmp_path)
    service.inspect("9.0.0", "stable")
    service.state_store.save(UpdaterState(current_version=current))
    with pytest.raises((UpdateError, ManifestError)):
        service.stage("b" * 32, "9.0.0", selected=True)
    cast(Mock, service.source).download.assert_not_called()


def test_selected_release_checks_rollback_compatibility(tmp_path):
    service = helper(tmp_path)
    cast(Mock, service.source).release.return_value = SignedManifest(
        ReleaseManifest.parse(manifest(rollback_schema_min=3, rollback_schema_max=3)), b""
    )
    with pytest.raises(ManifestError, match="roll settings back"):
        service.inspect("9.0.0", "stable")


@pytest.mark.parametrize("action", ["inspect", "stage-selected"])
def test_selection_protocol_accepts_version_only_and_rejects_ambient_authority(action):
    raw = {
        "protocol": 1,
        "action": action,
        "token": "x",
        "request_id": "a" * 32,
        "release": "9.0.0",
        "channel": "stable",
    }
    assert Request.parse(json.dumps(raw).encode(), "x").release == "9.0.0"
    for field in ("url", "manifest", "capabilities"):
        with pytest.raises(ProtocolError):
            Request.parse(json.dumps({**raw, field: "untrusted"}).encode(), "x")


def managed_app(tmp_path, *, modern=True):
    app = create_app(tmp_path, demo_mode=True)
    controller = app.state.updater
    controller.installation = ManagedInstallation(True, "Managed", tmp_path)
    controller.helper = Mock()
    status = {"ok": True, "phase": "idle", "current_version": "8.0.0"}
    if modern:
        status["capabilities"] = [SELECTED_RELEASES_CAPABILITY]
    controller.helper.call.side_effect = lambda action, *_args, **_kwargs: (
        {"ok": True, "manifest": manifest()}
        if action == "inspect"
        else {"ok": True, "phase": "queued"}
        if action == "stage-selected"
        else status
    )
    return app, controller


def browser_session(client):
    response = client.post("/api/updates/session", json={}, headers={"Origin": "http://testserver"})
    return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf"]}


def test_selected_web_actions_require_origin_csrf_and_accept_no_client_authority(tmp_path):
    app, controller = managed_app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/updates/inspect", json={"release": "9.0.0"}).status_code == 403
    headers = browser_session(client)
    assert (
        client.post("/api/updates/inspect", json={"release": "9.0.0"}, headers=headers).status_code
        == 200
    )
    assert controller.helper.call.call_args.kwargs == {"timeout": 40}
    for field in ("url", "manifest", "capabilities"):
        result = client.post(
            "/api/updates/stage-selected",
            json={"release": "9.0.0", field: "untrusted"},
            headers=headers,
        )
        assert result.status_code == 400
    assert (
        client.post(
            "/api/updates/stage-selected", json={"release": "9.0.0"}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post("/api/updates/activate", json={"release": "9.0.0"}, headers=headers).status_code
        == 400
    )


def test_legacy_helper_disables_selection_and_fresh_capability_is_required(tmp_path):
    _app, controller = managed_app(tmp_path, modern=False)
    status = controller.status()
    assert not status["release_selection_supported"]
    assert "administrator" in status["release_selection_reason"]
    with pytest.raises(ValueError, match="administrator"):
        controller.action("stage-selected", "9.0.0")
    controller.helper.call.side_effect = lambda *_args, **_kwargs: {
        "ok": False,
        "message": "unsupported protocol action",
    }
    with pytest.raises(ValueError):
        controller.action("stage-selected", "9.0.0")
    assert all(call.args[0] == "status" for call in controller.helper.call.call_args_list)


def test_failed_inspection_clears_prior_selection_and_keeps_regular_poll_responsive(tmp_path):
    _, controller = managed_app(tmp_path)
    controller.inspect_release("9.0.0")
    assert controller.status()["selected_manifest"]["version"] == "9.0.0"
    controller.helper.call.side_effect = OSError("helper disconnected")
    with pytest.raises(ValueError):
        controller.inspect_release("9.0.1")
    assert controller.selected_manifest is None


def test_stale_capability_does_not_bypass_a_replaced_legacy_helper(tmp_path):
    _app, controller = managed_app(tmp_path)

    def replaced(action, *_args, **_kwargs):
        if action == "status":
            return {"ok": True, "capabilities": [SELECTED_RELEASES_CAPABILITY]}
        return {"ok": False, "message": "unsupported protocol action"}

    controller.helper.call.side_effect = replaced
    with pytest.raises(ValueError, match="unsupported"):
        controller.action("stage-selected", "9.0.0")


def test_unreachable_helper_does_not_claim_an_administrator_upgrade_is_needed(tmp_path):
    _app, controller = managed_app(tmp_path)
    controller.helper.call.side_effect = OSError("temporarily unavailable")
    status = controller.status()
    assert not status["release_selection_supported"]
    assert "Reconnect" in status["release_selection_reason"]


def test_catalog_is_one_bounded_cached_metadata_fetch_without_manifest_downloads(
    tmp_path, monkeypatch
):
    _, controller = managed_app(tmp_path)
    requests = []
    payload = [metadata(f"9.0.{index}") for index in range(120)]
    native_client = httpx.Client

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "photoframe.updater.controller.httpx.Client",
        lambda **_kwargs: native_client(transport=httpx.MockTransport(handler)),
    )
    first = controller.release_catalog()
    second = controller.release_catalog()
    assert first == second
    assert len(first["releases"]) == 12
    assert first["releases"][0]["version"] == "9.0.99"
    assert all(row["compatibility"] == "Not verified" for row in first["releases"])
    assert len(requests) == 1 and "per_page=100" in requests[0]
    assert all(call.args[0] == "status" for call in controller.helper.call.call_args_list)


def test_catalog_describes_channel_age_and_missing_bundle_without_authorizing_it(
    tmp_path, monkeypatch
):
    _, controller = managed_app(tmp_path)
    monkeypatch.setattr("photoframe.updater.controller.__version__", "9.0.0.dev2")
    controller.preferences["channel"] = "develop"
    controller.catalog_checked_at = float("inf")
    controller.catalog_cache = [
        metadata("9.0.0.dev3"),
        metadata("9.0.0.dev2"),
        metadata("9.0.0.dev1"),
        metadata("9.0.0.dev4", assets=[]),
        metadata("9.0.0"),
    ]
    rows = {row["version"]: row for row in controller.release_catalog()["releases"]}
    assert set(rows) == {"9.0.0.dev1", "9.0.0.dev2", "9.0.0.dev3", "9.0.0.dev4"}
    assert all(row["channel"] == "develop" for row in rows.values())
    assert rows["9.0.0.dev3"]["relationship"] == "newer"
    assert rows["9.0.0.dev3"]["available"]
    assert rows["9.0.0.dev3"]["compatibility"] == "Not verified"
    assert rows["9.0.0.dev2"]["relationship"] == "installed"
    assert rows["9.0.0.dev1"]["relationship"] == "older"
    assert rows["9.0.0.dev1"]["bundle_available"]
    assert not rows["9.0.0.dev1"]["available"]
    assert rows["9.0.0.dev4"]["relationship"] == "newer"
    assert not rows["9.0.0.dev4"]["bundle_available"]
    assert not rows["9.0.0.dev4"]["available"]
    assert all(call.args[0] == "status" for call in controller.helper.call.call_args_list)
