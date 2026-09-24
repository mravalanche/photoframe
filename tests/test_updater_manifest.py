import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from photoframe.updater.manifest import (
    ManifestError,
    ReleaseManifest,
    SignedManifest,
    canonical_json,
)


def manifest(bundle: bytes = b"bundle") -> dict[str, object]:
    return {
        "version": "1.3.0",
        "commit": "a" * 40,
        "repository": "mravalanche/photoframe",
        "platform": "linux-aarch64",
        "bundle": "photoframe-1.3.0-linux-aarch64.tar.gz",
        "bundle_size": len(bundle),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "updater_protocol": 1,
        "settings_schema_min": 2,
        "settings_schema_max": 3,
        "rollback_schema_min": 2,
        "rollback_schema_max": 2,
        "release_notes": "Safer updates.",
        "published_at": datetime.now(UTC).isoformat(),
    }


def signed(raw: dict[str, object], key: Ed25519PrivateKey) -> bytes:
    return json.dumps(
        {
            "manifest": raw,
            "signature": base64.b64encode(key.sign(canonical_json(raw))).decode(),
        }
    ).encode()


def test_signed_manifest_binds_identity_policy_and_bundle(tmp_path: Path) -> None:
    bundle = b"immutable bundle"
    path = tmp_path / "bundle.tar.gz"
    path.write_bytes(bundle)
    private = Ed25519PrivateKey.generate()
    result = SignedManifest.verify(signed(manifest(bundle), private), private.public_key())

    result.manifest.require_upgrade_from("1.2.1")
    result.manifest.supports_rollback_to(2)
    result.verify_bundle(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", "1.3.0-rc.1", "stable or"),
        ("repository", "attacker/photoframe", "wrong repository"),
        ("platform", "linux-x86_64", "different platform"),
        ("commit", "abc", "full lowercase"),
        ("bundle", "../photoframe.tar.gz", "bundle name"),
        ("bundle_size", 600 * 1024 * 1024, "size"),
        ("updater_protocol", 2, "updater protocol"),
        ("settings_schema_min", 3, "installed settings schema"),
    ],
)
def test_manifest_rejects_wrong_identity_channel_and_compatibility(
    field: str, value: object, message: str
) -> None:
    raw = manifest()
    raw[field] = value
    with pytest.raises(ManifestError, match=message):
        ReleaseManifest.parse(raw)


def test_signature_and_digest_are_enforced(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    payload = bytearray(signed(manifest(), private))
    payload[-5] ^= 1
    with pytest.raises(ManifestError, match="signature"):
        SignedManifest.verify(bytes(payload), private.public_key())

    verified = SignedManifest.verify(signed(manifest(), private), private.public_key())
    bundle = tmp_path / "bundle"
    bundle.write_bytes(b"tamper")
    with pytest.raises(ManifestError, match=r"digest|size"):
        verified.verify_bundle(bundle)


def test_downgrade_and_incompatible_rollback_are_rejected() -> None:
    release = ReleaseManifest.parse(manifest())
    with pytest.raises(ManifestError, match="not newer"):
        release.require_upgrade_from("1.3.0")
    with pytest.raises(ManifestError, match="roll settings back"):
        release.supports_rollback_to(3)
