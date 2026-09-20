import argparse
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from photoframe.updater import bootstrap as module
from photoframe.updater.bundle import release_identity


def test_trusted_path_rejects_writable_ancestor():
    parent = Mock()
    parent.is_symlink.return_value = False
    parent.exists.return_value = True
    parent.stat.return_value = SimpleNamespace(st_uid=1000, st_mode=0o755)
    child = Mock()
    child.parents = (parent,)
    child.is_symlink.return_value = False
    child.exists.return_value = False
    with pytest.raises(ValueError, match="non-root"):
        module.trusted_path(child)
    parent.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o775)
    with pytest.raises(ValueError, match="non-root"):
        module.trusted_path(child)
    parent.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o755)
    module.trusted_path(child)
    parent.is_symlink.return_value = True
    with pytest.raises(ValueError, match="symlink"):
        module.trusted_path(child)


def test_platform_rejects_old_libc(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(module.platform, "libc_ver", lambda: ("glibc", "2.31"))
    with pytest.raises(ValueError, match=r"2\.36"):
        module.check_platform()
    monkeypatch.setattr(module.platform, "libc_ver", lambda: ("glibc", "2.36"))
    module.check_platform()


def test_custom_python_is_retained_in_helper_unit(monkeypatch):
    monkeypatch.setattr(module, "validate_path", lambda path: path)
    _, helper = module.service_units(
        Path("/opt/photoframe"),
        Path("/var/lib/photoframe"),
        "pi",
        "pi",
        Path("/opt/python/bin/python3.12"),
    )
    assert f"--python {Path('/opt/python/bin/python3.12')}" in helper


@pytest.fixture
def migration(monkeypatch, tmp_path):
    root, data = tmp_path / "managed", tmp_path / "data"
    data.mkdir()
    settings = data / "settings.toml"
    settings.write_bytes(b"# old-settings")
    units = tmp_path / "units"
    units.mkdir()
    old_unit = units / "photoframe.service"
    old_unit.write_bytes(b"old-unit")
    layout = module.InstallLayout(tmp_path / "config", tmp_path / "state/state.json", units)
    python = tmp_path / "python"
    python.write_bytes(b"python")
    manifest, public_key = tmp_path / "manifest", tmp_path / "key"
    manifest.write_bytes(b"manifest")
    public_key.write_bytes(b"key")
    (tmp_path / "bundle").write_bytes(b"bundle")
    args = argparse.Namespace(
        user="pi",
        root=root,
        data_dir=data,
        python=python,
        manifest=manifest,
        public_key=public_key,
        bundle=tmp_path / "bundle",
    )
    account = SimpleNamespace(pw_uid=1234, pw_gid=1234)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda user: account))
    monkeypatch.setitem(
        sys.modules, "grp", SimpleNamespace(getgrgid=lambda gid: SimpleNamespace(gr_name="pi"))
    )
    monkeypatch.setattr(module, "check_platform", lambda: None)
    monkeypatch.setattr(module, "validate_path", lambda path: path)
    monkeypatch.setattr(module, "trusted_path", lambda path: None)
    original_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if path == data:
            return SimpleNamespace(st_uid=1234, st_mode=info.st_mode)
        return info

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(
        Path, "symlink_to", lambda path, *args, **kwargs: path.write_bytes(b"pointer")
    )
    ownership = []
    monkeypatch.setattr(module.os, "chown", lambda *args: ownership.append(args), raising=False)
    read_saved = module.SavedFile.read
    monkeypatch.setattr(module.SavedFile, "read", lambda path, **kwargs: read_saved(path))
    restore_owned = module.restore_owned_file

    def restore_recorded(path, payload, mode, uid, gid):
        ownership.append((path, uid, gid))
        restore_owned(path, payload, mode, uid, gid)

    monkeypatch.setattr(module, "restore_owned_file", restore_recorded)
    key = Mock()
    key.public_bytes.return_value = b"key"
    monkeypatch.setattr(module, "load_public_key", lambda path: key)
    signed = Mock()
    signed.manifest.version = "1.3.0"
    signed.manifest.as_dict.return_value = release_identity("1.3.0", "a" * 40)
    monkeypatch.setattr(module.SignedManifest, "verify", lambda *args: signed)

    def extract(archive, slot):
        (slot / "release.json").write_text(json.dumps(release_identity("1.3.0", "a" * 40)))
        (slot / "wheelhouse").mkdir()

    monkeypatch.setattr(module, "safe_extract", extract)
    monkeypatch.setattr(module, "install_environment", lambda *args: None)
    monkeypatch.setattr(module, "service_enabled", lambda name: False)
    calls = []
    monkeypatch.setattr(module, "run", calls.append)
    monkeypatch.setattr(module, "wait_ready", lambda *args: None)
    return args, layout, calls, ownership


def test_failed_readiness_restores_old_install_and_allows_retry(migration, monkeypatch):
    args, layout, calls, ownership = migration
    settings = args.data_dir / "settings.toml"
    original = settings.stat()

    def fail_ready(data, version):
        assert version == "1.3.0"
        settings.write_bytes(b"new-incompatible-settings")
        raise RuntimeError("failed readiness")

    monkeypatch.setattr(module, "wait_ready", fail_ready)
    with pytest.raises(RuntimeError, match="failed readiness"):
        module.bootstrap(args, layout=layout)
    assert settings.read_bytes() == b"# old-settings"
    assert (layout.units / "photoframe.service").read_bytes() == b"old-unit"
    assert (settings, original.st_uid, original.st_gid) in ownership
    assert not args.root.exists()
    assert not layout.config.exists()
    assert not layout.state.exists()
    assert not (layout.units / "photoframe-updater.service").exists()
    assert ["systemctl", "disable", "photoframe-updater.service"] in calls
    assert ["systemctl", "disable", "photoframe.service"] in calls
    assert calls[-1] == ["systemctl", "start", "photoframe.service"]
    monkeypatch.setattr(module, "wait_ready", lambda *args: None)
    module.bootstrap(args, layout=layout)
    assert (args.root / "current").exists()
    assert not (layout.config / "update-pin.hash").exists()


def test_staging_failure_does_not_stop_existing_service(migration, monkeypatch):
    args, layout, calls, _ = migration

    def fail_install(*args):
        raise RuntimeError("wheel install failed")

    monkeypatch.setattr(module, "install_environment", fail_install)
    with pytest.raises(RuntimeError, match="wheel install failed"):
        module.bootstrap(args, layout=layout)
    assert not any("stop" in call for call in calls)
    assert not args.root.exists()
    assert (args.data_dir / "settings.toml").read_bytes() == b"# old-settings"


@pytest.mark.parametrize("value", ["tls/cert.pem", "~/cert.pem"])
def test_relative_supplied_tls_paths_block_migration(tmp_path, value):
    (tmp_path / "settings.toml").write_text(f'[network]\ncertificate_path = "{value}"\n')
    with pytest.raises(ValueError, match="absolute paths"):
        module.check_certificate_paths(tmp_path)


def test_saved_file_rejects_non_regular_settings(tmp_path):
    with pytest.raises(ValueError, match="regular"):
        module.SavedFile.read(tmp_path)
    path = tmp_path / "settings"
    path.write_bytes(b"settings")
    saved = module.SavedFile.read(path)
    assert saved is not None
    assert saved.mode == stat.S_IMODE(path.stat().st_mode)
    with pytest.raises(ValueError, match="owned"):
        module.SavedFile.read(path, expected_uid=path.stat().st_uid + 1)
