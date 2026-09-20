"""Web-side updater coordination; privileged work stays in the helper."""

from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import suppress
from pathlib import Path
from threading import RLock
from uuid import uuid4

import httpx

from .. import __version__
from ..persistence import atomic_write
from .managed import detect_managed_installation
from .manifest import semver
from .protocol import HelperClient


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
        self.last_error = None
        self.activation_job = self.preferences.get("activation_job")
        self.public_release = None
        self.public_checked_at = 0
        if self.installation.enabled:
            self.save()
        if self.activation_job:
            self.runtime.maintenance_gate.enter_and_drain(0)

    def save(self):
        atomic_write(self.path, json.dumps(self.preferences).encode())

    def status(self):
        with self.lock:
            return self._status()

    def _status(self):
        result = {"phase": "idle", "message": self.installation.reason}
        if self.installation.enabled:
            try:
                result = self.helper.call("status", uuid4().hex)
                if (
                    self.activation_job
                    and result.get("job_id") == self.activation_job
                    and result.get("phase") in {"failed", "rolled_back", "complete"}
                ):
                    self.runtime.maintenance_gate.cancel()
                    self.activation_job = None
                    self.preferences.pop("activation_job", None)
                    self.preferences.pop("activation_action", None)
                    self.preferences.pop("activation_release", None)
                    self.save()
            except (OSError, ValueError) as exc:
                result = {"phase": "unavailable", "message": f"Updater unavailable: {exc}"}
        return {
            **result,
            "running_version": __version__,
            "managed": self.installation.enabled,
            "weekly": self.preferences.get("weekly", False),
            "next_check": self.preferences.get("next_check"),
            "last_check": self.preferences.get("last_check"),
            "check_error": self.last_error,
            "public_release": self.public_release,
        }

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

    def configure(self, enabled):
        with self.lock:
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
                    self.helper.call(
                        self.preferences["activation_action"],
                        self.activation_job,
                        self.preferences.get("activation_release"),
                    )
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

    def action(self, action, release=None, request_id=None):
        if not self.installation.enabled:
            raise ValueError(
                "Web updates require a managed installation. Follow the migration guide to enable them."
            )
        with self.lock:
            request_id = request_id or uuid4().hex
            if self.activation_job and request_id != self.activation_job:
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
                result = self.helper.call(action, request_id, release)
                if result.get("ok") is False:
                    self.activation_job = None
                    self.preferences.pop("activation_job", None)
                    self.save()
                    self.runtime.maintenance_gate.cancel()
                    raise ValueError(result.get("message", "Updater request failed"))
                return result
            except (OSError, ValueError):
                raise
