"""Strict signed release-manifest parsing and policy enforcement."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

OFFICIAL_REPOSITORY = "mravalanche/photoframe"
SUPPORTED_PROTOCOL = 1
SUPPORTED_SETTINGS_SCHEMA = 2
MAX_MANIFEST_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BUNDLE = re.compile(r"^photoframe-(\d+\.\d+\.\d+)-linux-aarch64\.tar\.gz$")


class ManifestError(ValueError):
    """A release is malformed, untrusted, or incompatible."""


def semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if not match:
        raise ManifestError("only stable semantic versions are accepted")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    commit: str
    repository: str
    platform: str
    bundle: str
    bundle_size: int
    bundle_sha256: str
    updater_protocol: int
    settings_schema_min: int
    settings_schema_max: int
    rollback_schema_min: int
    rollback_schema_max: int
    release_notes: str
    published_at: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> ReleaseManifest:
        expected = set(cls.__dataclass_fields__)
        if set(raw) != expected:
            raise ManifestError("manifest fields do not match the supported schema")
        try:
            result = cls(**raw)
        except TypeError as exc:
            raise ManifestError("manifest has invalid fields") from exc
        result.validate()
        return result

    def validate(self) -> None:
        integer_fields = {
            "bundle_size",
            "updater_protocol",
            "settings_schema_min",
            "settings_schema_max",
            "rollback_schema_min",
            "rollback_schema_max",
        }
        for name in self.__dataclass_fields__:
            expected = int if name in integer_fields else str
            if type(getattr(self, name)) is not expected:
                raise ManifestError(f"manifest field {name} has an invalid type")
        semver(self.version)
        if self.repository != OFFICIAL_REPOSITORY:
            raise ManifestError("release belongs to the wrong repository")
        if self.platform != "linux-aarch64":
            raise ManifestError("release targets a different platform")
        if not _COMMIT.fullmatch(self.commit):
            raise ManifestError("release commit must be a full lowercase Git identity")
        bundle_match = _BUNDLE.fullmatch(self.bundle)
        if not bundle_match or bundle_match.group(1) != self.version:
            raise ManifestError("bundle name does not match the release version and platform")
        if not isinstance(self.bundle_size, int) or not 1 <= self.bundle_size <= MAX_BUNDLE_BYTES:
            raise ManifestError("bundle size is outside the supported limit")
        if not re.fullmatch(r"[0-9a-f]{64}", self.bundle_sha256):
            raise ManifestError("bundle digest is not a SHA-256 value")
        if self.updater_protocol != SUPPORTED_PROTOCOL:
            raise ManifestError("release requires an incompatible updater protocol")
        for name in (
            "settings_schema_min",
            "settings_schema_max",
            "rollback_schema_min",
            "rollback_schema_max",
        ):
            if not isinstance(getattr(self, name), int) or getattr(self, name) < 1:
                raise ManifestError("schema compatibility values must be positive integers")
        if self.settings_schema_min > self.settings_schema_max:
            raise ManifestError("release settings schema range is invalid")
        if self.rollback_schema_min > self.rollback_schema_max:
            raise ManifestError("release rollback schema range is invalid")
        if not self.settings_schema_min <= SUPPORTED_SETTINGS_SCHEMA <= self.settings_schema_max:
            raise ManifestError("release cannot read the installed settings schema")
        try:
            published = datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ManifestError("release publication time is invalid") from exc
        if published.tzinfo is None or published.astimezone(UTC).utcoffset() is None:
            raise ManifestError("release publication time must include a timezone")
        if len(self.release_notes) > 32_000:
            raise ManifestError("release notes exceed the supported limit")

    def require_upgrade_from(self, current_version: str) -> None:
        if semver(self.version) <= semver(current_version):
            raise ManifestError("release is not newer than the running version")

    def supports_rollback_to(self, previous_schema: int) -> None:
        if not self.rollback_schema_min <= previous_schema <= self.rollback_schema_max:
            raise ManifestError("release cannot safely roll settings back to the retained version")

    def identity(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in (
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
        }

    def as_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True)
class SignedManifest:
    manifest: ReleaseManifest
    signature: bytes

    @classmethod
    def verify(cls, payload: bytes, public_key: Ed25519PublicKey) -> SignedManifest:
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ManifestError("signed manifest exceeds the supported limit")
        try:
            envelope = json.loads(payload)
            if not isinstance(envelope, dict) or set(envelope) != {"manifest", "signature"}:
                raise ManifestError("signed manifest envelope is invalid")
            raw = envelope["manifest"]
            if not isinstance(raw, dict) or not isinstance(envelope["signature"], str):
                raise ManifestError("signed manifest envelope is invalid")
            signature = base64.b64decode(envelope["signature"], validate=True)
            public_key.verify(signature, canonical_json(raw))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, InvalidSignature) as exc:
            if isinstance(exc, ManifestError):
                raise
            raise ManifestError("release manifest signature is invalid") from exc
        return cls(ReleaseManifest.parse(raw), signature)

    def verify_bundle(self, path: Path) -> None:
        if path.stat().st_size != self.manifest.bundle_size:
            raise ManifestError("downloaded bundle size does not match the signed manifest")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != self.manifest.bundle_sha256:
            raise ManifestError("downloaded bundle digest does not match the signed manifest")
