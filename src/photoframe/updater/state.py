"""Durable updater state with crash-safe atomic writes."""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..persistence import atomic_write
from .manifest import _SEMVER, validate_channel


@dataclass
class UpdaterState:
    phase: str = "idle"
    job_id: str | None = None
    current_version: str | None = None
    previous_version: str | None = None
    staged_version: str | None = None
    message: str = "Ready"
    progress: int = 0
    rollback_attempted: bool = False
    action: str | None = None
    target_version: str | None = None
    snapshot_id: str | None = None
    snapshot_ready: bool = False
    settings_existed: bool = False
    settings_uid: int | None = None
    settings_gid: int | None = None
    request_fingerprints: dict[str, str] = field(default_factory=dict)
    latest_manifest: dict[str, Any] | None = None
    latest_channel: str = "stable"
    cached_at: str | None = None
    completed_requests: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UpdaterState:
        accepted = {name: raw[name] for name in cls.__dataclass_fields__ if name in raw}
        for name in ("current_version", "previous_version", "staged_version", "target_version"):
            value = accepted.get(name)
            if value is not None and (not isinstance(value, str) or not _SEMVER.fullmatch(value)):
                raise ValueError("invalid persisted release version")
        for name in ("job_id", "snapshot_id"):
            value = accepted.get(name)
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value)
            ):
                raise ValueError("invalid persisted job identity")
        for name in ("completed_requests", "request_fingerprints"):
            if name in accepted and not isinstance(accepted[name], dict):
                raise ValueError("invalid persisted request history")
        validate_channel(accepted.get("latest_channel", "stable"))
        return cls(**accepted)

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> UpdaterState:
        try:
            raw = json.loads(self.path.read_bytes())
            if not isinstance(raw, dict):
                raise ValueError
            return UpdaterState.from_dict(raw)
        except FileNotFoundError:
            return UpdaterState()
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError("updater state is corrupt; manual recovery is required") from exc

    def save(self, state: UpdaterState) -> None:
        atomic_write(
            self.path,
            json.dumps(state.as_dict(), sort_keys=True, separators=(",", ":")).encode(),
            mode=stat.S_IRUSR | stat.S_IWUSR,
        )

    def remember(self, state: UpdaterState, request_id: str, result: dict[str, Any]) -> None:
        state.completed_requests[request_id] = result
        while len(state.completed_requests) > 50:
            del state.completed_requests[next(iter(state.completed_requests))]
        self.save(state)
