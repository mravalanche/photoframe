import io
import os
import stat
import tarfile
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from photoframe.updater.helper import (
    UpdateError,
    Updater,
    make_release_readable,
    privileged_environment,
    restore_owned_file,
    safe_extract,
)
from photoframe.updater.protocol import Request
from photoframe.updater.state import StateStore, UpdaterState


@pytest.mark.skipif(os.name != "posix", reason="POSIX release permissions")
def test_release_permissions_override_private_umask_without_changing_symlink_target(tmp_path):
    old_mask = os.umask(0o077)
    try:
        slot = tmp_path / "release"
        library = slot / "venv/lib/python3.12/site-packages/example.py"
        library.parent.mkdir(parents=True)
        library.write_text("ready = True")
        executable = slot / "venv/bin/photoframe"
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o700)
        external = tmp_path / "system-python"
        external.write_text("python")
        external.chmod(0o700)
        (executable.parent / "python").symlink_to(external)
        make_release_readable(slot)
    finally:
        os.umask(old_mask)
    assert stat.S_IMODE(library.stat().st_mode) == 0o644
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
    for directory in (slot, *library.parents):
        if directory == tmp_path:
            break
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert stat.S_IMODE(external.stat().st_mode) == 0o700


def test_privileged_commands_ignore_inherited_python_and_search_paths(monkeypatch):
    monkeypatch.setenv("PATH", "/app/bin")
    monkeypatch.setenv("PYTHONPATH", "/app/modules")
    monkeypatch.setenv("PYTHONHOME", "/app/python")
    environment = privileged_environment()
    assert environment["PATH"] == "/usr/sbin:/usr/bin:/sbin:/bin"
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor-based restore")
def test_restore_replaces_symlink_without_changing_target_owner_or_mode(tmp_path):
    external = tmp_path / "protected"
    external.write_bytes(b"protected")
    external.chmod(0o644)
    settings = tmp_path / "settings.toml"
    settings.symlink_to(external)
    owner = external.stat()
    restore_owned_file(settings, b"restored", 0o600, owner.st_uid, owner.st_gid)
    assert settings.read_bytes() == b"restored"
    assert not settings.is_symlink()
    assert external.read_bytes() == b"protected"
    assert stat.S_IMODE(external.stat().st_mode) == 0o644
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600


def updater(tmp_path: Path) -> Updater:
    return Updater(
        tmp_path,
        tmp_path / "data",
        StateStore(tmp_path / "state.json"),
        Mock(),
        command=Mock(),
        health_check=lambda _: True,
    )


def test_async_job_stays_observable_and_duplicate_is_idempotent(tmp_path: Path) -> None:
    service = updater(tmp_path)
    started, finish = threading.Event(), threading.Event()

    def check(request_id: str) -> dict:
        started.set()
        assert finish.wait(5)
        return {}

    service.check = check
    request = Request(1, "check", "token", "a" * 32)
    try:
        assert service.dispatch(request)["accepted"]
        assert started.wait(2)
        assert service.status()["job_id"] == request.request_id
        assert service.dispatch(request)["accepted"]
        with pytest.raises(UpdateError, match="already active"):
            service.dispatch(Request(1, "stage", "token", "b" * 32, "1.3.0"))
    finally:
        finish.set()


def test_request_identity_cannot_be_reused_for_different_action(tmp_path: Path) -> None:
    service = updater(tmp_path)
    state = UpdaterState(
        request_fingerprints={"a" * 32: "check:"}, completed_requests={"a" * 32: {"ok": True}}
    )
    service.state_store.save(state)
    with pytest.raises(UpdateError, match="different operation"):
        service.dispatch(Request(1, "rollback", "token", "a" * 32))


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("symlink", tarfile.SYMTYPE),
        ("device", tarfile.CHRTYPE),
    ],
)
def test_archive_rejects_unsafe_entries(tmp_path: Path, name: str, kind: bytes) -> None:
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        item = tarfile.TarInfo(name)
        item.type = kind
        handle.addfile(item, io.BytesIO())
    with pytest.raises(UpdateError, match="unsafe"):
        safe_extract(archive, tmp_path / "out")


def test_interrupted_activation_rolls_back_only_once(tmp_path: Path) -> None:
    service = updater(tmp_path)
    switched = []
    service._switch = lambda version: switched.append(version)
    service.state_store.save(
        UpdaterState(
            phase="activating",
            job_id="a" * 32,
            previous_version="1.2.1",
            current_version="1.3.0",
            target_version="1.3.0",
        )
    )
    service.recover()
    state = service.state_store.load()
    assert state.phase == "rolled_back"
    assert state.current_version == "1.2.1"
    assert switched == ["1.2.1"]
    service.recover()
    assert switched == ["1.2.1"]


def test_interrupted_rollback_requires_manual_recovery(tmp_path: Path) -> None:
    service = updater(tmp_path)
    service.state_store.save(
        UpdaterState(phase="rolling_back", job_id="a" * 32, rollback_attempted=True)
    )
    service.recover()
    assert service.status()["phase"] == "failed"
    assert "Manual recovery" in service.status()["message"]
    assert isinstance(service.command, Mock)
    service.command.assert_not_called()


def test_failed_rollback_health_is_not_reported_as_success(tmp_path: Path) -> None:
    service = updater(tmp_path)
    service._switch = Mock()
    service._wait_healthy = lambda version: False
    state = UpdaterState(previous_version="1.2.1")
    with pytest.raises(UpdateError, match="restored service failed"):
        service._restore(state, "a" * 32, "1.3.0", "failure")
    assert state.rollback_attempted


def test_snapshot_restores_owner_and_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = updater(tmp_path)
    service._switch = Mock()
    service.data_dir.mkdir()
    service.snapshots.mkdir()
    (service.data_dir / "settings.toml").write_text("new")
    (service.snapshots / f"{'a' * 32}.settings.toml").write_text("old")
    chown = Mock()
    monkeypatch.setattr("os.chown", chown, raising=False)
    fchown = Mock()
    monkeypatch.setattr("os.fchown", fchown, raising=False)
    state = UpdaterState(
        previous_version="1.2.1",
        settings_existed=True,
        snapshot_ready=True,
        settings_uid=1000,
        settings_gid=1000,
        snapshot_id="a" * 32,
    )
    service._restore(state, "a" * 32, "1.3.0", "failure")
    assert (service.data_dir / "settings.toml").read_text() == "old"
    if os.name == "posix":
        assert fchown.call_args.args[1:] == (1000, 1000)
        chown.assert_not_called()
    else:
        chown.assert_called_once_with(service.data_dir / "settings.toml", 1000, 1000)


@pytest.mark.parametrize(
    "field,value",
    [("protocol", True), ("action", []), ("token", 1), ("request_id", None), ("release", {})],
)
def test_protocol_rejects_malformed_types(field: str, value: object) -> None:
    import json

    from photoframe.updater.protocol import ProtocolError

    payload = {
        "protocol": 1,
        "action": "stage",
        "token": "token",
        "request_id": "a" * 32,
        "release": "1.3.0",
    }
    payload[field] = value
    with pytest.raises(ProtocolError):
        Request.parse(json.dumps(payload).encode(), "token")


def test_stage_installs_offline_at_final_path_then_marks_ready(tmp_path: Path) -> None:
    import hashlib
    import json

    from photoframe.updater.manifest import ReleaseManifest, SignedManifest

    service = updater(tmp_path)
    identity = {
        "version": "1.3.0",
        "commit": "a" * 40,
        "repository": "mravalanche/photoframe",
        "platform": "linux-aarch64",
        "updater_protocol": 1,
        "settings_schema_min": 2,
        "settings_schema_max": 2,
        "rollback_schema_min": 2,
        "rollback_schema_max": 2,
    }
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for name, content in [
            ("release.json", json.dumps(identity).encode()),
            ("wheelhouse/photoframe.whl", b"wheel"),
        ]:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            handle.addfile(item, io.BytesIO(content))
    data = archive.read_bytes()
    manifest = ReleaseManifest.parse(
        {
            **identity,
            "bundle": "photoframe-1.3.0-linux-aarch64.tar.gz",
            "bundle_size": len(data),
            "bundle_sha256": hashlib.sha256(data).hexdigest(),
            "release_notes": "",
            "published_at": "2026-09-20T00:00:00Z",
        }
    )
    service.source.latest = Mock(return_value=SignedManifest(manifest, b""))
    service.source.download = Mock(side_effect=lambda manifest, target: target.write_bytes(data))
    result = service.stage("a" * 32, "1.3.0")
    assert result["phase"] == "staged"
    slot = tmp_path / "versions/1.3.0"
    assert (slot / ".ready").exists()
    assert isinstance(service.command, Mock)
    args = service.command.call_args_list
    assert args[0].args[0] == ["/usr/bin/python3.12", "-m", "venv", str(slot / "venv")]
    assert "--no-index" in args[1].args[0]
    assert "photoframe[inky]==1.3.0" in args[1].args[0]
    assert not (tmp_path / "current").exists()


def test_activation_failure_restores_snapshot_and_verifies_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = updater(tmp_path)
    service._switch = Mock()
    monkeypatch.setattr("photoframe.updater.helper.time.sleep", lambda _: None)
    service.health_check = lambda _: False
    service._wait_healthy = Mock(return_value=True)
    service.state_store.save(UpdaterState(current_version="1.2.1", staged_version="1.3.0"))
    result = service.activate("a" * 32, "1.3.0")
    assert result["phase"] == "rolled_back"
    assert service.state_store.load().current_version == "1.2.1"
    service._wait_healthy.assert_called_once_with("1.2.1")


@pytest.mark.parametrize("version,expected", [("1.3.0", True), ("1.2.1", False)])
def test_runtime_health_uses_saved_port_and_expected_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, expected: bool
) -> None:
    from photoframe.updater.helper import runtime_healthy

    (tmp_path / "settings.toml").write_text('[network]\nport = 9123\nprotocol = "http"\n')
    connection = Mock()
    response = connection.getresponse.return_value
    response.status = 200
    response.read.return_value = b'{"ready":true,"version":"1.3.0"}'
    factory = Mock(return_value=connection)
    monkeypatch.setattr("photoframe.updater.helper.http.client.HTTPConnection", factory)
    assert runtime_healthy(tmp_path, version) is expected
    factory.assert_called_once_with("127.0.0.1", 9123, timeout=5)
    connection.request.assert_called_once_with("GET", "/health/update")
    connection.close.assert_called_once()


def test_runtime_https_pins_certificate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from photoframe.updater.helper import runtime_healthy

    (tmp_path / "settings.toml").write_text('[network]\nport = 9443\nprotocol = "https"\n')
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls/photoframe-local.crt").write_text("certificate")
    connection = Mock()
    connection.sock.getpeercert.return_value = b"wrong certificate"
    context = Mock(verify_flags=0)
    monkeypatch.setattr(
        "photoframe.updater.helper.ssl.create_default_context", Mock(return_value=context)
    )
    monkeypatch.setattr(
        "photoframe.updater.helper.x509.load_pem_x509_certificate",
        Mock(return_value=Mock(public_bytes=Mock(return_value=b"expected"))),
    )
    factory = Mock(return_value=connection)
    monkeypatch.setattr("photoframe.updater.helper.http.client.HTTPSConnection", factory)
    assert not runtime_healthy(tmp_path, "1.3.0")
    factory.assert_called_once_with("127.0.0.1", 9443, timeout=5, context=context)
    connection.request.assert_not_called()
    connection.close.assert_called_once()


def test_activation_stops_before_snapshot_and_starts_after_switch(tmp_path: Path) -> None:
    service = updater(tmp_path)
    events: list[str] = []
    service.command = lambda argv: events.append(argv[1])

    def snapshot(job_id: str) -> Path | None:
        events.append("snapshot")
        return None

    service._snapshot_settings = snapshot
    service._switch = lambda version: events.append("switch")
    service._prune_versions = Mock()
    service.state_store.save(UpdaterState(current_version="1.2.1", staged_version="1.3.0"))
    assert service.activate("a" * 32, "1.3.0")["phase"] == "complete"
    assert events == ["stop", "snapshot", "switch", "start"]


def test_crash_before_snapshot_preserves_existing_settings(tmp_path: Path) -> None:
    service = updater(tmp_path)
    service._switch = Mock()
    service.data_dir.mkdir()
    (service.data_dir / "settings.toml").write_text("original")
    service.state_store.save(
        UpdaterState(
            phase="activating", previous_version="1.2.1", job_id="a" * 32, snapshot_ready=False
        )
    )
    service.recover()
    assert (service.data_dir / "settings.toml").read_text() == "original"


def test_supplied_certificate_resolves_like_managed_app(tmp_path: Path) -> None:
    from photoframe.updater.helper import _certificate_path

    assert _certificate_path(tmp_path, "certs/server.crt") == tmp_path / "certs/server.crt"
    assert _certificate_path(tmp_path, str(tmp_path / "absolute.crt")) == tmp_path / "absolute.crt"


def test_reapply_current_release_finishes_durable_job(tmp_path: Path) -> None:
    service = updater(tmp_path)
    service.state_store.save(UpdaterState(phase="queued", current_version="1.3.0"))
    assert service.activate("a" * 32, "1.3.0")["phase"] == "complete"
    assert service.status()["phase"] == "complete"
    assert service.status()["staged_version"] is None
