"""Observable, cooperative album preparation with an atomic commit boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from uuid import uuid4


class AlbumLoadCancelled(Exception):
    """The user cancelled before any candidate catalog was published."""


class AlbumLoad:
    def __init__(
        self, album_id: str, album_name: str | None, *, waiting: bool = False, refresh: bool = False
    ):
        self._lock = RLock()
        self._cancelled = False
        self.state = {
            "id": str(uuid4()),
            "active": True,
            "phase": "waiting" if waiting else "loading",
            "message": "Waiting for the previous photo request to finish"
            if waiting
            else "Getting the photo list from your library",
            "completed": 0,
            "total": None,
            "album_id": album_id,
            "album_name": album_name,
            "cancellable": True,
            "refresh": refresh,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def checkpoint(self) -> None:
        with self._lock:
            if self._cancelled:
                raise AlbumLoadCancelled

    def cancel(self) -> bool:
        with self._lock:
            if not self.state["active"] or not self.state["cancellable"]:
                return False
            self._cancelled = True
            self.state.update(
                phase="cancelling",
                cancellable=False,
                message="Stopping after the current photo request finishes. Your existing album stays unchanged.",
            )
            return True

    def stage(self, phase: str, message: str) -> None:
        with self._lock:
            self.checkpoint()
            self.state.update(phase=phase, message=message)

    def progress(self, completed: int, total: int) -> None:
        with self._lock:
            self.checkpoint()
            self.state.update(
                phase="checking",
                completed=completed,
                total=total,
                message=f"Prepared {completed} of {total} photos for this frame",
            )

    @contextmanager
    def committing(self) -> Iterator[None]:
        # Cancellation and publication are ordered by this lock. A successful
        # cancel can never race a late settings/catalog commit.
        with self._lock:
            self.checkpoint()
            self.state.update(
                phase="saving", cancellable=False, message="Saving the prepared album"
            )
            yield

    def finish(self, phase: str, message: str) -> None:
        with self._lock:
            if self._cancelled:
                phase, message = (
                    "cancelled",
                    "Album loading cancelled. Your existing album and frame are unchanged.",
                )
            self.state.update(active=False, cancellable=False, phase=phase, message=message)
