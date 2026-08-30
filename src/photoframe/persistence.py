"""Durable, atomic file persistence helpers."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def _fsync_directory(directory: Path) -> None:
    """Commit a changed directory entry where directory fsync is supported."""
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    """Atomically replace *path* with a fully flushed same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            with suppress(OSError):
                temporary.chmod(mode)
        os.replace(temporary, path)
        if mode is not None:
            with suppress(OSError):
                path.chmod(mode)
        _fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
