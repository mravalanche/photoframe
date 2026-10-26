"""Web-side updater coordination; privileged work stays in the helper."""

from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import suppress
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

import httpx

from .. import __version__
from ..persistence import atomic_write
from .managed import detect_managed_installation
from .manifest import ReleaseManifest, release_channel, require_channel, semver, validate_channel
from .protocol import SELECTED_RELEASES_CAPABILITY, HelperClient

RELEASE_LIST_URL = "https://api.github.com/repos/mravalanche/photoframe/releases?per_page=100"
MAX_LIST_BYTES = 2 * 1024 * 1024
MAX_PICKER_RELEASES = 12


class UpdateController:
    def __init__(self, data_dir, runtime):
        self.runtime = runtime
        self.installation = detect_managed_installation()
        self.helper = HelperClient(
            Path(os.getenv("PHOTOFRAME_UPDATER_SOCKET", "/run/photoframe/updater.sock")),
            Path(os.getenv("PHOTOFRAME_UPDATER_TOKEN", "/etc/photoframe/updater.token")),
        )
        self.path = data_dir / "updater-preferences.json"
        self.lock = RLock()
        try:
            self.preferences = json.loads(self.path.read_text())
            if (
                not isinstance(self.preferences, dict)
                or type(self.preferences.get("weekly")) is not bool
                or not isinstance(self.preferences.get("next_check"), (int, float))
            ):
                raise ValueError("Invalid updater preferences")
        except FileNotFoundError:
            self.preferences = {
                "weekly": True,
                "next_check": time.time() + secrets.randbelow(86400),
            }
        except (ValueError, OSError):
            self.preferences = {"weekly": False, "next_check": 0}
        self.preferences.setdefault("channel", "stable")
        if self.preferences["channel"] not in {"stable", "develop"}:
            self.preferences["channel"] = "stable"
        self.last_error = None
        self.activation_job = self.preferences.get("activation_job")
        self.public_release = None
        self.public_checked_at = 0
        self.catalog_cache = None
        self.catalog_checked_at = 0
        self.selected_manifest = None
        if self.installation.enabled:
            self.save()
        if self.activation_job:
            self.runtime.maintenance_gate.enter_and_drain(0)

    def save(self):
        atomic_write(self.path, json.dumps(self.preferences).encode())

    def _finish_activation(self):
        previous = self.preferences.copy()
        for key in ("activation_job", "activation_action", "activation_release"):
            self.preferences.pop(key, None)
        try:
            self.save()
        except OSError:
            self.preferences = previous
            raise
        self.activation_job = None
        self.runtime.maintenance_gate.cancel()

    def status(self):
        with self.lock:
            return self._status()

    def _status(self):
        result: dict[str, Any] = {"phase": "idle", "message": self.installation.reason}
        if self.installation.enabled:
            try:
                result = self.helper.call("status", uuid4().hex)
                if (
                    self.activation_job
                    and result.get("job_id") == self.activation_job
                    and result.get("phase") in {"failed", "rolled_back", "complete"}
                ):
                    self._finish_activation()
            except (OSError, ValueError) as exc:
                result = {"phase": "unavailable", "message": f"Updater unavailable: {exc}"}
        channel = self.preferences["channel"]
        if result.get("latest_channel", "stable") != channel:
            result["latest_manifest"] = None
        if result.get("staged_version") and release_channel(result["staged_version"]) != channel:
            result["staged_version"] = None
        latest = result.get("latest_manifest")
        capabilities = result.get("capabilities")
        selection_supported = (
            self.installation.enabled
            and isinstance(capabilities, list)
            and SELECTED_RELEASES_CAPABILITY in capabilities
        )
        upgrade_available = False
        if latest:
            try:
                require_channel(latest["version"], channel)
                upgrade_available = semver(latest["version"]) > semver(__version__)
            except (ValueError, KeyError, TypeError):
                result["latest_manifest"] = None
        return {
            **result,
            "channel": channel,
            "upgrade_available": upgrade_available,
            "running_version": __version__,
            "managed": self.installation.enabled,
            "weekly": self.preferences.get("weekly", False),
            "next_check": self.preferences.get("next_check"),
            "last_check": (
                self.preferences.get("last_check")
                if self.installation.enabled
                else self.public_checked_at or None
            ),
            "check_error": self.last_error,
            "public_release": self.public_release,
            "release_selection_supported": selection_supported,
            "release_selection_reason": (
                ""
                if selection_supported
                else "Reconnect to the updater to check release-selection support."
                if result.get("phase") == "unavailable"
                else "A managed installation is required to install a selected release."
                if not self.installation.enabled
                else "An administrator must update the privileged helper to enable release selection. Latest-release updates remain available."
            ),
            "selected_manifest": self.selected_manifest,
        }

    def release_catalog(self):
        """Bounded metadata discovery; entries do not authorize installation."""
        with self.lock:
            channel = self.preferences["channel"]
            if self.catalog_cache is None or time.time() - self.catalog_checked_at >= 60:
                with (
                    httpx.Client(timeout=10, follow_redirects=False) as client,
                    client.stream(
                        "GET", RELEASE_LIST_URL, headers={"Accept": "application/vnd.github+json"}
                    ) as response,
                ):
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_LIST_BYTES:
                            raise ValueError("Release listing exceeds the supported limit")
                raw = json.loads(data)
                if not isinstance(raw, list):
                    raise ValueError("Invalid release listing")
                self.catalog_cache = raw[:100]
                self.catalog_checked_at = time.time()
            rows = []
            for release in self.catalog_cache:
                if (
                    not isinstance(release, dict)
                    or release.get("draft") is not False
                    or release.get("prerelease") is not (channel == "develop")
                ):
                    continue
                tag = release.get("tag_name")
                if not isinstance(tag, str) or not tag.startswith("v"):
                    continue
                version = tag[1:]
                try:
                    require_channel(version, channel)
                except ValueError:
                    continue
                assets = release.get("assets", [])
                names = (
                    {
                        asset.get("name")
                        for asset in assets
                        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
                    }
                    if isinstance(assets, list)
                    else set()
                )
                complete = {
                    "photoframe-manifest.json",
                    f"photoframe-{version}-linux-aarch64.tar.gz",
                } <= names
                reason = (
                    "Not newer than the running version"
                    if semver(version) <= semver(__version__)
                    else "Signed bundle is not published"
                    if not complete
                    else ""
                )
                published = release.get("published_at")
                rows.append(
                    {
                        "version": version,
                        "published_at": published[:64] if isinstance(published, str) else None,
                        "available": not reason,
                        "reason": reason,
                        "compatibility": reason or "Not verified",
                    }
                )
            rows.sort(key=lambda row: semver(row["version"]), reverse=True)
            status = self._status()
            return {
                "channel": channel,
                "releases": rows[:MAX_PICKER_RELEASES],
                "selection_supported": status["release_selection_supported"],
                "reason": status["release_selection_reason"],
            }

    def inspect_release(self, release):
        with self.lock:
            self.selected_manifest = None
            self._require_release_selection(release)
            channel = self.preferences["channel"]
        result = self.helper.call("inspect", uuid4().hex, release, channel, timeout=40)
        with self.lock:
            if channel != self.preferences["channel"]:
                raise ValueError("The update channel changed; verify the release again")
            if result.get("ok") is not True or not isinstance(result.get("manifest"), dict):
                raise ValueError(result.get("message", "Release verification failed"))
            manifest = ReleaseManifest.parse(result["manifest"])
            require_channel(manifest.version, self.preferences["channel"])
            manifest.require_upgrade_from(__version__)
            manifest.supports_rollback_to(2)
            if manifest.version != release:
                raise ValueError("Helper verified a different release")
            self.selected_manifest = manifest.as_dict()
            return {"manifest": self.selected_manifest, "channel": self.preferences["channel"]}

    def _require_release_selection(self, release):
        if not isinstance(release, str):
            raise ValueError("Choose a release version")
        require_channel(release, self.preferences["channel"])
        if semver(release) <= semver(__version__):
            raise ValueError("Choose a release newer than the running version")
        status = self._status()
        if not status["release_selection_supported"]:
            raise ValueError(status["release_selection_reason"])

    def check_public_release(self):
        """Read-only discovery for source installations; never downloads code."""
        with self.lock:
            if time.time() - self.public_checked_at < 3600:
                return self.status()
            self.public_checked_at = time.time()
            try:
                with httpx.Client(timeout=10, follow_redirects=False) as client:
                    response = client.get(
                        "https://api.github.com/repos/mravalanche/photoframe/releases/latest",
                        headers={"Accept": "application/vnd.github+json"},
                    )
                    response.raise_for_status()
                    release = response.json()
                version = release["tag_name"].removeprefix("v")
                semver(version)
                if release.get("prerelease") or release.get("draft"):
                    raise ValueError("No stable release is available")
                self.public_release = version
                self.last_error = None
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                self.last_error = f"Could not check releases: {exc}"
            return self.status()

    def configure(self, enabled, channel=None):
        with self.lock:
            channel = validate_channel(
                channel if channel is not None else self.preferences["channel"]
            )
            if self.activation_job or self.status().get("phase") in {
                "queued",
                "checking",
                "staging",
                "activating",
                "rolling_back",
            }:
                raise ValueError(
                    "Wait for the current update operation before changing preferences"
                )
            if channel != self.preferences["channel"]:
                self.selected_manifest = None
                self.public_release = None
                self.public_checked_at = 0
                self.last_error = None
            self.preferences["channel"] = channel
            self.preferences["weekly"] = enabled
            self.preferences["next_check"] = time.time() + 7 * 86400 + secrets.randbelow(43201)
            self.save()
        return self.status()

    def tick(self):
        with self.lock:
            return self._tick()

    def _tick(self):
        if self.activation_job:
            status = self.status()
            if (
                self.activation_job
                and status.get("phase") != "unavailable"
                and status.get("job_id") != self.activation_job
            ):
                with suppress(OSError, ValueError):
                    result = self._helper_action(
                        self.preferences["activation_action"],
                        self.activation_job,
                        self.preferences.get("activation_release"),
                    )
                    if result.get("ok") is False or result.get("phase") in {
                        "complete",
                        "rolled_back",
                        "failed",
                    }:
                        if result.get("ok") is False:
                            self.last_error = result.get("message", "Updater request failed")
                        self._finish_activation()
            return True
        if (
            not self.installation.enabled
            or not self.preferences.get("weekly")
            or time.time() < self.preferences.get("next_check", 0)
        ):
            return False
        try:
            self.action("check")
            self.last_error = None
        except (OSError, ValueError, RuntimeError) as exc:
            self.last_error = str(exc)
        return True

    def _helper_action(self, action, request_id, release=None):
        if self.preferences["channel"] == "stable":
            return self.helper.call(action, request_id, release)
        return self.helper.call(action, request_id, release, "develop")

    def action(self, action, release=None, request_id=None):
        if not self.installation.enabled:
            raise ValueError(
                "Web updates require a managed installation. Follow the migration guide to enable them."
            )
        if action in {"stage", "stage-selected", "activate"}:
            if not isinstance(release, str):
                raise ValueError("A release version is required")
            require_channel(release, self.preferences["channel"])
        elif release is not None:
            raise ValueError("A release version is only allowed for release-specific actions")
        with self.lock:
            if action == "stage-selected":
                self._require_release_selection(release)
            request_id = request_id or uuid4().hex
            if self.activation_job and (
                request_id != self.activation_job
                or action != self.preferences.get("activation_action")
                or release != self.preferences.get("activation_release")
            ):
                raise ValueError("An update restart is already in progress")
            if action == "check":
                self.preferences["last_check"] = time.time()
                self.preferences["next_check"] = time.time() + 7 * 86400 + secrets.randbelow(43201)
                self.save()
            if action in {"activate", "rollback"}:
                if not self.runtime.maintenance_gate.enter_and_drain(30):
                    raise ValueError(
                        "Frame work is still running. Wait for it to finish and retry."
                    )
                if self.runtime.renderer.hardware_busy:
                    self.runtime.maintenance_gate.cancel()
                    raise ValueError(
                        "The physical display is busy. Wait for the frame update to finish and retry."
                    )
                self.activation_job = request_id
                self.preferences.update(
                    activation_job=request_id, activation_action=action, activation_release=release
                )
                try:
                    self.save()
                except OSError:
                    self.activation_job = None
                    for key in ("activation_job", "activation_action", "activation_release"):
                        self.preferences.pop(key, None)
                    self.runtime.maintenance_gate.cancel()
                    raise
            try:
                result = self._helper_action(action, request_id, release)
                if result.get("ok") is False:
                    if self.activation_job:
                        self._finish_activation()
                    raise ValueError(result.get("message", "Updater request failed"))
                if self.activation_job and result.get("phase") in {
                    "complete",
                    "rolled_back",
                    "failed",
                }:
                    self._finish_activation()
                return result
            except (OSError, ValueError):
                raise
