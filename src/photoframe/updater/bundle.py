"""Deterministic release archives; private signing material is supplied only by CI."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .manifest import OFFICIAL_REPOSITORY, ReleaseManifest, canonical_json, semver

IDENTITY_FIELDS = (
    "version",
    "commit",
    "repository",
    "platform",
    "updater_protocol",
    "settings_schema_min",
    "settings_schema_max",
    "rollback_schema_min",
    "rollback_schema_max",
)


def release_identity(version: str, commit: str) -> dict[str, object]:
    semver(version)
    return {
        "version": version,
        "commit": commit,
        "repository": OFFICIAL_REPOSITORY,
        "platform": "linux-aarch64",
        "updater_protocol": 1,
        "settings_schema_min": 2,
        "settings_schema_max": 2,
        "rollback_schema_min": 2,
        "rollback_schema_max": 2,
    }


def build_bundle(wheelhouse: Path, output: Path, version: str, commit: str) -> Path:
    """Archive reviewed wheel bytes in stable order without CI paths or timestamps."""
    identity = release_identity(version, commit)
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels or any(path.is_symlink() for path in wheels):
        raise ValueError("a regular-file wheelhouse is required")
    app = [path for path in wheels if path.name.startswith(f"photoframe-{version}-")]
    if len(app) != 1:
        raise ValueError("wheelhouse must contain exactly the requested Photoframe wheel")
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            if archive.testzip() is not None:
                raise ValueError("wheelhouse contains a corrupt wheel")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"photoframe-{version}-linux-aarch64.tar.gz"
    # Never silently replace already produced release bytes.
    with (
        target.open("xb") as handle,
        gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, filename="") as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive,
    ):
        entries = [("release.json", canonical_json(identity))]
        entries.extend((f"wheelhouse/{wheel.name}", wheel.read_bytes()) for wheel in wheels)
        for name, payload in entries:
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(payload), 0o644, 0
            member.uid = member.gid = 0
            archive.addfile(member, io.BytesIO(payload))
    return target


def sign_bundle(
    bundle: Path,
    *,
    version: str,
    commit: str,
    published_at: str,
    notes: str,
    private_key: bytes,
    public_key: bytes,
) -> bytes:
    key = serialization.load_pem_private_key(private_key, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("release signing requires an Ed25519 key")
    trusted = serialization.load_pem_public_key(public_key)
    if key.public_key().public_bytes_raw() != trusted.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ):
        raise ValueError("signing key does not match the independently pinned public key")
    raw = {
        **release_identity(version, commit),
        "bundle": bundle.name,
        "bundle_size": bundle.stat().st_size,
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "release_notes": notes,
        "published_at": published_at,
    }
    ReleaseManifest.parse(raw)
    return canonical_json(
        {"manifest": raw, "signature": base64.b64encode(key.sign(canonical_json(raw))).decode()}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--published-at", default=datetime.now(UTC).isoformat())
    args = parser.parse_args()
    secret = os.environ.pop("PHOTOFRAME_RELEASE_SIGNING_KEY", "")
    if not secret:
        parser.error("PHOTOFRAME_RELEASE_SIGNING_KEY is required; unsigned releases are refused")
    # Validate key configuration before producing a bundle.
    public_key = args.public_key.read_bytes()
    bundle = build_bundle(args.wheelhouse, args.output, args.version, args.commit)
    payload = sign_bundle(
        bundle,
        version=args.version,
        commit=args.commit,
        published_at=args.published_at,
        notes=args.notes.read_text(),
        private_key=secret.encode(),
        public_key=public_key,
    )
    (args.output / "photoframe-manifest.json").write_bytes(payload)
    digest = json.loads(payload)["manifest"]["bundle_sha256"]
    (args.output / "SHA256SUMS").write_text(f"{digest}  {bundle.name}\n", encoding="ascii")


if __name__ == "__main__":
    main()
