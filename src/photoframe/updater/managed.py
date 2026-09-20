"""Managed-layout detection that never mistakes a source checkout for an appliance."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManagedInstallation:
    enabled: bool
    reason: str
    root: Path | None = None


def detect_managed_installation() -> ManagedInstallation:
    root_value = os.getenv("PHOTOFRAME_MANAGED_ROOT")
    slot_value = os.getenv("PHOTOFRAME_RELEASE_SLOT")
    if not root_value or not slot_value:
        return ManagedInstallation(False, "Source and developer installations never self-modify")
    root = Path(root_value)
    slot = Path(slot_value)
    try:
        if not root.is_absolute() or not slot.is_absolute():
            raise ValueError
        if slot.parent.resolve() != (root / "versions").resolve():
            raise ValueError
        current = root / "current"
        if not current.is_symlink() or current.resolve() != slot.resolve():
            raise ValueError
        if (slot / ".git").exists():
            raise ValueError
    except (OSError, ValueError):
        return ManagedInstallation(False, "Managed installation markers are invalid")
    return ManagedInstallation(True, "Managed systemd installation", root)
