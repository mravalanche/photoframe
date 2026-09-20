import io
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from photoframe.updater.bootstrap import install_environment, service_units, validate_path
from photoframe.updater.bundle import build_bundle, release_identity, sign_bundle
from photoframe.updater.manifest import SignedManifest


def wheelhouse(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    with zipfile.ZipFile(wheels / "photoframe-1.3.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("photoframe/__init__.py", '__version__ = "1.3.0"')
    return wheels


def test_archive_is_deterministic_and_identity_is_not_circular(tmp_path):
    wheels = wheelhouse(tmp_path)
    first = build_bundle(wheels, tmp_path / "first", "1.3.0", "a" * 40)
    second = build_bundle(wheels, tmp_path / "second", "1.3.0", "a" * 40)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(first.read_bytes()), mode="r:gz") as archive:
        identity = archive.extractfile("release.json")
        assert identity is not None
        assert json.load(identity) == release_identity("1.3.0", "a" * 40)
        assert "bundle_sha256" not in release_identity("1.3.0", "a" * 40)
    with pytest.raises(FileExistsError):
        build_bundle(wheels, tmp_path / "first", "1.3.0", "a" * 40)


def test_signed_bundle_round_trip_and_wrong_key_refusal(tmp_path):
    bundle = build_bundle(wheelhouse(tmp_path), tmp_path / "out", "1.3.0", "a" * 40)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    options: dict[str, Any] = dict(
        version="1.3.0",
        commit="a" * 40,
        published_at="2026-09-20T00:00:00Z",
        notes="Example release",
        private_key=private,
        public_key=public,
    )
    signed = SignedManifest.verify(sign_bundle(bundle, **options), key.public_key())
    signed.verify_bundle(bundle)
    other = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    options["public_key"] = other
    with pytest.raises(ValueError, match="independently pinned"):
        sign_bundle(bundle, **options)


@pytest.mark.skipif(__import__("os").name != "posix", reason="Linux managed install paths")
def test_units_separate_root_helper_and_app_environment():
    app, helper = service_units(Path("/opt/photoframe"), Path("/var/lib/photoframe"), "pi", "pi")
    assert "User=pi\n" in app
    assert "/current/venv/bin/python -m photoframe.updater.launch" in app
    assert "User=root\n" in helper
    assert "/helper-venv/bin/python -m photoframe.updater.helper" in helper
    assert "ReadWritePaths=/var/lib/photoframe\n" in app
    for value in ["/etc", "/opt/frame%h", "/opt/frame\nExecStart=bad", "/var/lib"]:
        with pytest.raises(ValueError):
            validate_path(Path(value))


def test_install_environment_is_offline_and_installs_hardware_extra(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr("photoframe.updater.bootstrap.run", commands.append)
    python = tmp_path / "python"
    destination = tmp_path / "slot/venv"
    wheels = tmp_path / "slot/wheelhouse"
    install_environment(python, destination, wheels, "1.3.0")
    assert commands[0] == [str(python), "-m", "venv", str(destination)]
    assert "--no-index" in commands[1]
    assert "--isolated" in commands[1]
    assert "photoframe[inky]==1.3.0" in commands[1]
    assert commands[-1][-1] == "check"
