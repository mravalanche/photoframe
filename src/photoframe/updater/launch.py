"""Resolve the active immutable slot on every systemd application start."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    root = Path(os.environ["PHOTOFRAME_MANAGED_ROOT"])
    slot = (root / "current").resolve(strict=True)
    if slot.parent != (root / "versions").resolve() or not (slot / ".ready").is_file():
        raise RuntimeError("active managed release is not a verified ready slot")
    os.environ["PHOTOFRAME_RELEASE_SLOT"] = str(slot)
    python = slot / "venv/bin/python"
    # This interpreter is inside the verified root-owned active release slot.
    os.execv(str(python), [str(python), "-m", "photoframe"])  # nosec B606


if __name__ == "__main__":
    main()
