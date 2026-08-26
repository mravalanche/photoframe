"""A bounded on-disk cache for provider originals.

The cache is provider-neutral: callers use a stable provider/photo identifier and
the provider remains responsible for acquiring a missing original.  Files are
evicted least-recently-used before a write, so a temporary large asset cannot
silently grow an SD card without bound.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CacheStats:
    files: int
    bytes: int


class PhotoCache:
    def __init__(self, data_dir: Path, max_bytes: int) -> None:
        self.path = data_dir / "photo-cache"
        self.max_bytes = max_bytes

    @staticmethod
    def _name(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest() + ".image"

    def _file(self, key: str) -> Path:
        return self.path / self._name(key)[:2] / self._name(key)

    def get(self, key: str) -> bytes | None:
        path = self._file(key)
        try:
            content = path.read_bytes()
            # atime is unreliable on many Pi filesystems; use mtime as an LRU
            # marker instead, without changing the file contents.
            os.utime(path, None)
            return content
        except FileNotFoundError:
            return None

    def stats(self) -> CacheStats:
        try:
            entries = [p for p in self.path.rglob("*.image") if p.is_file()]
        except FileNotFoundError:
            return CacheStats(0, 0)
        return CacheStats(len(entries), sum(p.stat().st_size for p in entries))

    def put(self, key: str, content: bytes) -> None:
        if len(content) > self.max_bytes:
            # An original larger than the complete cache cannot be safely kept.
            return
        target = self._file(key)
        existing = target.stat().st_size if target.exists() else 0
        self._evict_until(self.max_bytes - len(content) + existing, exclude=target)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".photo-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _evict_until(self, permitted_bytes: int, exclude: Path) -> None:
        stats = self.stats()
        if stats.bytes <= permitted_bytes:
            return
        files = sorted(
            (p for p in self.path.rglob("*.image") if p != exclude),
            key=lambda p: p.stat().st_mtime,
        )
        remaining = stats.bytes
        for path in files:
            if remaining <= permitted_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                remaining -= size
            except FileNotFoundError:
                pass
