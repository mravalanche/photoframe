"""Root-owned updater service with a deliberately narrow local protocol."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import shutil
import socketserver
import ssl
import stat
import subprocess  # nosec B404 - fixed systemctl argv only
import tarfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..persistence import _fsync_directory, atomic_write
from .manifest import MAX_BUNDLE_BYTES, ManifestError, ReleaseManifest, SignedManifest
from .protocol import MAX_MESSAGE_BYTES, ProtocolError, Request
from .state import StateStore, UpdaterState

MANIFEST_URL = (
    "https://github.com/mravalanche/photoframe/releases/latest/download/photoframe-manifest.json"
)
BUNDLE_URL = "https://github.com/mravalanche/photoframe/releases/download/v{version}/{bundle}"
MIN_FREE_AFTER_STAGE = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def privileged_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    return environment


def make_release_readable(slot: Path) -> None:
    """Publish root-created files for the service user without following venv links."""
    slot.chmod(0o755)
    for directory, folders, files in os.walk(slot, topdown=False, followlinks=False):
        for name in (*folders, *files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            if stat.S_ISDIR(mode):
                path.chmod(0o755)
            elif stat.S_ISREG(mode):
                path.chmod(0o755 if mode & 0o111 else 0o644)
                with path.open("r+b") as installed:
                    os.fsync(installed.fileno())
            else:
                raise UpdateError("release environment contains an unexpected file type")
        _fsync_directory(Path(directory))


def restore_owned_file(path: Path, payload: bytes, mode: int, uid: int, gid: int) -> None:
    """Restore into an app-owned directory without privileged path-following mutations."""
    if os.name != "posix":
        # Production helpers are Linux-only; retain portable unit-test support.
        atomic_write(path, payload, mode=mode)
        getattr(os, "chown")(path, uid, gid)  # noqa: B009
        return
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".{path.name}.{secrets.token_hex(16)}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fchown(output.fileno(), uid, gid)
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)
        os.close(directory)


def _certificate_path(data_dir: Path, value: str) -> Path:
    # The app runs in data_dir as its owner; resolve paths in that same context.
    if value == "~" or value.startswith("~/"):
        import pwd

        home = Path(getattr(pwd, "getpwuid")(data_dir.stat().st_uid).pw_dir)  # noqa: B009
        return home / value[2:] if value.startswith("~/") else home
    path = Path(value).expanduser()
    return path if path.is_absolute() else data_dir / path


def runtime_healthy(data_dir: Path, version: str) -> bool:
    """Probe only loopback, honoring the persisted listener and pinning its TLS identity."""
    connection: http.client.HTTPConnection | None = None
    try:
        settings = data_dir / "settings.toml"
        raw = tomllib.loads(settings.read_text()) if settings.exists() else {}
        network = raw.get("network", {})
        port = network.get("port", 8000)
        if type(port) is not int or not 1 <= port <= 65535:
            return False
        if network.get("protocol", "http") == "https":
            certificate = (
                data_dir / "tls/photoframe-local.crt"
                if network.get("certificate_mode", "automatic") == "automatic"
                else _certificate_path(data_dir, network.get("certificate_path", ""))
            )
            context = ssl.create_default_context(cafile=str(certificate))
            context.check_hostname = False  # Exact certificate pin below authenticates loopback.
            context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            secure = http.client.HTTPSConnection("127.0.0.1", port, timeout=5, context=context)
            connection = secure
            secure.connect()
            expected = x509.load_pem_x509_certificate(certificate.read_bytes()).public_bytes(
                serialization.Encoding.DER
            )
            if secure.sock is None or secure.sock.getpeercert(binary_form=True) != expected:
                return False
        else:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/health/update")
        response = connection.getresponse()
        payload = json.loads(response.read(16384))
        return (
            response.status == 200
            and isinstance(payload, dict)
            and payload.get("ready") is True
            and payload.get("version") == version
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, http.client.HTTPException):
        return False
    finally:
        if connection is not None:
            connection.close()


def load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise UpdateError(
            "official update signing key is not installed; managed updates remain disabled"
        ) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise UpdateError("official update signing key is not Ed25519")
    return key


def safe_extract(archive: Path, destination: Path) -> None:
    """Extract regular files/directories only, bounded and traversal-safe."""
    total = 0
    with tarfile.open(archive, "r:gz") as bundle:
        members = []
        for member in bundle:
            members.append(member)
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise UpdateError("release bundle contains too many entries")
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or member.size < 0
                or not path.parts
                or member.issym()
                or member.islnk()
                or member.isdev()
                or not (member.isfile() or member.isdir())
            ):
                raise UpdateError("release bundle contains an unsafe path or file type")
            total += member.size
            if total > MAX_EXPANDED_BYTES:
                raise UpdateError("release bundle expands beyond the supported limit")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise UpdateError("release bundle entry could not be read")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


class ReleaseSource:
    def __init__(
        self,
        public_key: Ed25519PublicKey,
        opener: Callable[..., BinaryIO] = urllib.request.urlopen,  # nosec B310 - fixed HTTPS URLs
    ) -> None:
        self.public_key = public_key
        self.opener = opener

    def latest(self) -> SignedManifest:
        try:
            with self.opener(MANIFEST_URL, timeout=20) as response:
                payload = response.read(256 * 1024 + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise UpdateError("could not reach the official release service") from exc
        return SignedManifest.verify(payload, self.public_key)

    def download(self, manifest: ReleaseManifest, target: Path) -> None:
        url = BUNDLE_URL.format(version=manifest.version, bundle=manifest.bundle)
        written = 0
        deadline = time.monotonic() + 15 * 60
        try:
            with self.opener(url, timeout=30) as response, target.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise UpdateError("release download exceeded its time limit")
                    written += len(chunk)
                    if written > manifest.bundle_size or written > MAX_BUNDLE_BYTES:
                        raise UpdateError("release download exceeded its signed size")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        except (OSError, urllib.error.URLError):
            target.unlink(missing_ok=True)
            raise
        if written != manifest.bundle_size:
            target.unlink(missing_ok=True)
            raise UpdateError("release download ended before its signed size")


class Updater:
    def __init__(
        self,
        root: Path,
        data_dir: Path,
        state_store: StateStore,
        source: ReleaseSource,
        *,
        service_name: str = "photoframe.service",
        health_url: str = "http://127.0.0.1:8000/health/update",
        python: str = "/usr/bin/python3.12",
        command: Callable[[list[str]], None] | None = None,
        health_check: Callable[[str], bool] | None = None,
    ) -> None:
        self.python = python
        self._operation_lock = threading.Lock()
        self.root = root
        self.data_dir = data_dir
        self.state_store = state_store
        self.source = source
        self.service_name = service_name
        self.health_url = health_url
        self.command = command or self._run_command
        self.health_check = health_check or self._health_check
        self.versions = root / "versions"
        self.staging = root / "staging"
        self.snapshots = root / "snapshots"

    @staticmethod
    def _run_command(argv: list[str]) -> None:
        subprocess.run(argv, check=True, timeout=600, env=privileged_environment())  # nosec B603

    @staticmethod
    def _health_check(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # nosec B310 - fixed local URL
                return response.status == 200 and json.load(response).get("ready") is True
        except (OSError, urllib.error.URLError):
            return False

    def _save_phase(
        self, state: UpdaterState, phase: str, message: str, progress: int
    ) -> UpdaterState:
        state.phase, state.message, state.progress = phase, message, progress
        self.state_store.save(state)
        return state

    def status(self) -> dict[str, Any]:
        state = self.state_store.load()
        payload = state.as_dict()
        payload.pop("completed_requests", None)
        payload.pop("request_fingerprints", None)
        return {"ok": True, **payload}

    def check(self, request_id: str) -> dict[str, Any]:
        state = self.state_store.load()
        if cached := state.completed_requests.get(request_id):
            return cached
        self._save_phase(state, "checking", "Checking the signed stable release channel", 10)
        try:
            signed = self.source.latest()
            state.latest_manifest = signed.manifest.as_dict()
            state.cached_at = datetime.now(UTC).isoformat()
            result = {
                "ok": True,
                "phase": "available",
                "release": state.latest_manifest,
                "cached": False,
            }
            state.phase, state.message, state.progress = "available", "Release check complete", 100
        except (UpdateError, ManifestError) as exc:
            if state.latest_manifest:
                result = {
                    "ok": True,
                    "phase": "offline",
                    "release": state.latest_manifest,
                    "cached": True,
                    "message": "Offline; showing the last authenticated release check",
                }
                state.phase, state.message = "offline", str(result["message"])
            else:
                result = {"ok": False, "phase": "failed", "message": str(exc)}
                state.phase, state.message = "failed", str(exc)
            state.progress = 100
        self.state_store.remember(state, request_id, result)
        return result

    def stage(self, request_id: str, release: str) -> dict[str, Any]:
        state = self.state_store.load()
        if cached := state.completed_requests.get(request_id):
            return cached
        if state.phase in {"staging", "activating", "rolling_back"}:
            raise UpdateError("another update operation is already active")
        signed = self.source.latest()
        manifest = signed.manifest
        if manifest.version != release:
            raise UpdateError("requested release is not the authenticated latest stable release")
        if state.current_version:
            manifest.require_upgrade_from(state.current_version)
        manifest.supports_rollback_to(2)
        target = self.versions / release
        if target.exists():
            if not (target / ".ready").is_file():
                raise UpdateError(
                    "existing release slot is incomplete; manual recovery is required"
                )
            state.phase = "staged"
            state.staged_version = release
            result = {"ok": True, "phase": "staged", "release": release, "idempotent": True}
            self.state_store.remember(state, request_id, result)
            return result
        usage = shutil.disk_usage(self.root)
        if usage.free < MAX_EXPANDED_BYTES * 2 + manifest.bundle_size + MIN_FREE_AFTER_STAGE:
            raise UpdateError("not enough free disk space to stage and retain rollback")
        self.staging.mkdir(parents=True, exist_ok=True)
        self.versions.mkdir(parents=True, exist_ok=True)
        self.versions.chmod(0o755)
        self._cleanup_interrupted_stage()
        self._save_phase(state, "staging", "Downloading release", 15)
        archive = self.staging / f".{release}.{request_id}.part"
        temporary = self.versions / f".{release}.{request_id}.tmp"
        try:
            self.source.download(manifest, archive)
            signed.verify_bundle(archive)
            self._save_phase(state, "staging", "Verified; preparing immutable release", 55)
            temporary.mkdir(mode=0o755)
            safe_extract(archive, temporary)
            metadata = temporary / "release.json"
            wheelhouse = temporary / "wheelhouse"
            if {entry.name for entry in temporary.iterdir()} != {"release.json", "wheelhouse"}:
                raise UpdateError("release bundle contains unexpected top-level entries")
            if not metadata.is_file() or not wheelhouse.is_dir():
                raise UpdateError("release bundle is missing its offline wheelhouse")
            installed = json.loads(metadata.read_bytes())
            if installed != manifest.identity():
                raise UpdateError("release slot metadata does not match its signed manifest")
            if any(not item.is_file() or item.suffix != ".whl" for item in wheelhouse.iterdir()):
                raise UpdateError("wheelhouse contains unexpected entries")
            if not list(wheelhouse.glob("*.whl")):
                raise UpdateError("release wheelhouse is empty")
            os.replace(temporary, target)
            self.command([self.python, "-m", "venv", str(target / "venv")])
            self.command(
                [
                    str(target / "venv/bin/python"),
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-cache-dir",
                    "--no-index",
                    "--only-binary=:all:",
                    "--find-links",
                    str(target / "wheelhouse"),
                    f"photoframe[inky]=={release}",
                ]
            )
            self.command([str(target / "venv/bin/python"), "-m", "pip", "--isolated", "check"])
            make_release_readable(target)
            atomic_write(target / ".ready", b"ready", mode=0o644)
            _fsync_directory(self.versions)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            if target.exists() and not (target / ".ready").exists():
                shutil.rmtree(target)
            raise
        finally:
            archive.unlink(missing_ok=True)
        state.staged_version = release
        result = {"ok": True, "phase": "staged", "release": release}
        state.phase, state.message, state.progress = "staged", "Ready to apply and restart", 100
        self.state_store.remember(state, request_id, result)
        return result

    def _cleanup_interrupted_stage(self) -> None:
        for path in self.staging.glob(".*.part"):
            path.unlink(missing_ok=True)
        for path in self.versions.glob(".*.tmp"):
            if path.is_dir():
                shutil.rmtree(path)

    def _switch(self, version: str) -> None:
        target = self.versions / version
        if not target.is_dir() or not (target / ".ready").is_file():
            raise UpdateError("release slot is not staged")
        temporary = self.root / ".current.next"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, self.root / "current")
        _fsync_directory(self.root)

    def _snapshot_settings(self, job_id: str) -> Path | None:
        settings = self.data_dir / "settings.toml"
        try:
            descriptor = os.open(
                settings, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.data_dir.stat().st_uid:
                raise UpdateError("settings must be a regular file owned by the application user")
            payload = source.read()
            self._snapshot_owner = (info.st_uid, info.st_gid)
        self.snapshots.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshots / f"{job_id}.settings.toml"
        atomic_write(snapshot, payload, mode=0o600)
        return snapshot

    def activate(self, request_id: str, release: str) -> dict[str, Any]:
        state = self.state_store.load()
        if cached := state.completed_requests.get(request_id):
            return cached
        if state.current_version == release:
            result = {"ok": True, "phase": "complete", "release": release, "idempotent": True}
            state.phase, state.message, state.progress = (
                "complete",
                "Release is already running",
                100,
            )
            state.staged_version = None
            self.state_store.remember(state, request_id, result)
            return result
        if state.staged_version != release:
            raise UpdateError("release has not been staged")
        old = state.current_version
        state.snapshot_id = request_id
        state.snapshot_ready = False
        state.job_id = request_id
        state.previous_version = old
        state.rollback_attempted = False
        self._save_phase(state, "activating", "Stopping service and snapshotting settings", 20)
        try:
            self.command(["systemctl", "stop", self.service_name])
            snapshot = self._snapshot_settings(request_id)
            state.settings_existed = snapshot is not None
            if snapshot is not None:
                state.settings_uid, state.settings_gid = self._snapshot_owner
            state.snapshot_ready = True
            self.state_store.save(state)
            self._switch(release)
            state.current_version = release
            self.state_store.save(state)
            self.command(["systemctl", "start", self.service_name])
            for _attempt in range(18):
                if self._healthy(release):
                    result = {"ok": True, "phase": "complete", "release": release}
                    state.phase, state.message, state.progress = "complete", "Update applied", 100
                    state.staged_version = None
                    self.state_store.remember(state, request_id, result)
                    self._prune_versions({release, old} - {None})
                    return result
                time.sleep(5)
            raise UpdateError("updated service did not become healthy in time")
        except (OSError, subprocess.SubprocessError, UpdateError) as exc:
            if old is None or state.rollback_attempted:
                state.phase, state.message = "failed", f"Manual recovery required: {exc}"
                self.state_store.save(state)
                raise UpdateError(state.message) from exc
            return self._restore(state, request_id, release, str(exc))

    def _healthy(self, version: str) -> bool:
        if self.health_check != self._health_check:
            return self.health_check(self.health_url)
        return runtime_healthy(self.data_dir, version)

    def _wait_healthy(self, version: str) -> bool:
        for _ in range(18):
            if self._healthy(version):
                return True
            time.sleep(5)
        return False

    def _restore(
        self, state: UpdaterState, request_id: str, release: str, reason: str
    ) -> dict[str, Any]:
        old = state.previous_version
        if old is None or state.rollback_attempted:
            raise UpdateError("Manual recovery required; automatic rollback is unavailable")
        state.rollback_attempted = True
        self._save_phase(state, "rolling_back", "Restoring known-good release", 70)
        self.command(["systemctl", "stop", self.service_name])
        self._switch(old)
        settings = self.data_dir / "settings.toml"
        snapshot = self.snapshots / f"{state.snapshot_id or request_id}.settings.toml"
        if state.snapshot_ready and state.settings_existed:
            if state.settings_uid is not None and state.settings_gid is not None:
                restore_owned_file(
                    settings, snapshot.read_bytes(), 0o600, state.settings_uid, state.settings_gid
                )
            else:
                raise UpdateError("settings snapshot has no recorded owner")
        elif state.snapshot_ready:
            settings.unlink(missing_ok=True)
        state.current_version = old
        self.state_store.save(state)
        self.command(["systemctl", "start", self.service_name])
        if not self._wait_healthy(old):
            raise UpdateError("Manual recovery required: restored service failed its health check")
        result = {
            "ok": False,
            "phase": "rolled_back",
            "release": release,
            "current_version": old,
            "message": f"Update rolled back: {reason}",
        }
        state.phase, state.message, state.progress = "rolled_back", str(result["message"]), 100
        state.previous_version = None
        self.state_store.remember(state, request_id, result)
        return result

    def rollback(self, request_id: str) -> dict[str, Any]:
        state = self.state_store.load()
        if cached := state.completed_requests.get(request_id):
            return cached
        previous = state.previous_version
        if not previous or previous == state.current_version:
            raise UpdateError("no known-good previous release is retained")
        self._save_phase(state, "rolling_back", "Restoring previous release", 30)
        self.command(["systemctl", "stop", self.service_name])
        self._switch(previous)
        settings = self.data_dir / "settings.toml"
        if state.snapshot_id and state.snapshot_ready:
            if state.settings_existed:
                snapshot = self.snapshots / f"{state.snapshot_id}.settings.toml"
                if state.settings_uid is not None and state.settings_gid is not None:
                    restore_owned_file(
                        settings,
                        snapshot.read_bytes(),
                        0o600,
                        state.settings_uid,
                        state.settings_gid,
                    )
                else:
                    raise UpdateError("settings snapshot has no recorded owner")
            else:
                settings.unlink(missing_ok=True)
        state.current_version = previous
        self.state_store.save(state)
        self.command(["systemctl", "start", self.service_name])
        if not self._wait_healthy(previous):
            raise UpdateError("Manual recovery required: restored service failed its health check")
        state.current_version, state.previous_version = previous, None
        state.snapshot_id = None
        result = {"ok": True, "phase": "complete", "release": previous}
        state.phase, state.message, state.progress = "complete", "Previous release restored", 100
        self.state_store.remember(state, request_id, result)
        return result

    def _prune_versions(self, keep: set[str]) -> None:
        for slot in self.versions.iterdir():
            if slot.is_dir() and not slot.name.startswith(".") and slot.name not in keep:
                shutil.rmtree(slot)

    def recover(self) -> None:
        """An interrupted activation fails closed and gets at most one rollback."""
        state = self.state_store.load()
        if state.phase not in {"queued", "checking", "staging", "activating", "rolling_back"}:
            return
        try:
            if state.phase == "activating" and state.job_id:
                self._restore(
                    state, state.job_id, state.target_version or "", "helper was interrupted"
                )
                return
            if state.phase == "rolling_back":
                raise UpdateError("Manual recovery required: rollback was interrupted")
            self._cleanup_interrupted_stage()
            if state.target_version:
                target = self.versions / state.target_version
                if target.exists() and not (target / ".ready").exists():
                    shutil.rmtree(target)
            raise UpdateError("Operation interrupted; retry with a new request ID")
        except (OSError, UpdateError, subprocess.SubprocessError) as exc:
            self._save_phase(state, "failed", str(exc), 100)
            if state.job_id:
                self.state_store.remember(
                    state, state.job_id, {"ok": False, "phase": "failed", "message": str(exc)}
                )

    def _work(self, request: Request) -> None:
        try:
            if request.action == "check":
                self.check(request.request_id)
            elif request.action == "stage":
                self.stage(request.request_id, request.release or "")
            elif request.action == "activate":
                self.activate(request.request_id, request.release or "")
            else:
                self.rollback(request.request_id)
        except Exception as exc:
            state = self.state_store.load()
            self._save_phase(state, "failed", str(exc), 100)
            self.state_store.remember(
                state, request.request_id, {"ok": False, "phase": "failed", "message": str(exc)}
            )
        finally:
            self._operation_lock.release()

    def dispatch(self, request: Request) -> dict[str, Any]:
        if request.action == "status":
            return self.status()
        fingerprint = f"{request.action}:{request.release or ''}"
        if not self._operation_lock.acquire(blocking=False):
            state = self.state_store.load()
            if (
                state.job_id == request.request_id
                and state.request_fingerprints.get(request.request_id) == fingerprint
            ):
                return {
                    "ok": True,
                    "accepted": True,
                    "job_id": request.request_id,
                    "phase": state.phase,
                }
            raise UpdateError("another update operation is already active")
        try:
            state = self.state_store.load()
            previous = state.request_fingerprints.get(request.request_id)
            if previous is not None and previous != fingerprint:
                raise UpdateError("request ID was already used for a different operation")
            if cached := state.completed_requests.get(request.request_id):
                self._operation_lock.release()
                return cached
            state.request_fingerprints[request.request_id] = fingerprint
            while len(state.request_fingerprints) > 50:
                del state.request_fingerprints[next(iter(state.request_fingerprints))]
            state.job_id, state.action, state.target_version = (
                request.request_id,
                request.action,
                request.release,
            )
            self._save_phase(state, "queued", "Operation accepted", 0)
            threading.Thread(target=self._work, args=(request,), daemon=True).start()
            return {"ok": True, "accepted": True, "job_id": request.request_id, "phase": "queued"}
        except Exception:
            self._operation_lock.release()
            raise


class HelperRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        if not isinstance(server, HelperServer):
            raise RuntimeError("Unexpected helper server")
        self.connection.settimeout(5)
        raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
        try:
            request = Request.parse(raw.rstrip(b"\n"), server.token)
            response = server.updater.dispatch(request)
        except (ProtocolError, UpdateError, ManifestError, OSError, ValueError) as exc:
            response = {"ok": False, "phase": "failed", "message": str(exc)}
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")


if TYPE_CHECKING:
    _UnixServer = socketserver.TCPServer
else:
    _UnixServer = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class HelperServer(socketserver.ThreadingMixIn, _UnixServer):
    daemon_threads = True

    def __init__(self, path: Path, updater: Updater, token: str):
        path.unlink(missing_ok=True)
        self.updater = updater
        self.token = token
        super().__init__(str(path), HelperRequestHandler)  # type: ignore[arg-type]


def main() -> None:
    parser = argparse.ArgumentParser(description="Photoframe managed updater helper")
    parser.add_argument("--root", type=Path, default=Path("/opt/photoframe"))
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/photoframe"))
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/photoframe-updater/state.json")
    )
    parser.add_argument("--socket", type=Path, default=Path("/run/photoframe/updater.sock"))
    parser.add_argument("--token", type=Path, default=Path("/etc/photoframe/updater.token"))
    parser.add_argument(
        "--public-key", type=Path, default=Path("/etc/photoframe/update-signing-key.pem")
    )
    parser.add_argument("--python", default="/usr/bin/python3.12")
    args = parser.parse_args()
    if os.name != "posix":
        raise SystemExit("The managed updater helper requires Linux")
    token = args.token.read_text().strip()
    source = ReleaseSource(load_public_key(args.public_key))
    updater = Updater(args.root, args.data_dir, StateStore(args.state), source, python=args.python)
    updater.recover()
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    with HelperServer(args.socket, updater, token) as server:
        # The dedicated application group may connect; token authenticates every request.
        os.chmod(args.socket, 0o660)  # nosec B103
        server.serve_forever()


if __name__ == "__main__":
    main()
