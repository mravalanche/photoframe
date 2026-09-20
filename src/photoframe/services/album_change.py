"""One observable album change at a time, without blocking the web server."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock, Thread
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .configuration import ConfigurationService


@dataclass
class AlbumChangeState:
    id: str | None = None
    active: bool = False
    phase: str = "idle"
    message: str = ""
    completed: int = 0
    total: int | None = None
    album_id: str | None = None
    album_name: str | None = None


class AlbumChangeJob:
    def __init__(self, configuration: ConfigurationService) -> None:
        self.configuration = configuration
        self._lock = Lock()
        self._state = AlbumChangeState()

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self._state)

    def start(self, album_id: str) -> dict:
        runtime = self.configuration.runtime
        albums, _photos = runtime.catalog_snapshot()
        album = next((item for item in albums if item.id == album_id), None)
        if album is None:
            raise ValueError("Choose an album from the loaded list")
        with self._lock:
            if self._state.active:
                raise ValueError("An album change is already in progress")
            runtime.claim_album_change()
            self._state = AlbumChangeState(
                id=str(uuid4()),
                active=True,
                phase="loading",
                message=f"Preparing {album.name}; waiting for any library refresh to finish",
                album_id=album.id,
                album_name=album.name,
            )
            try:
                Thread(
                    target=self._run, args=(album.id,), name="photoframe-album", daemon=True
                ).start()
            except Exception:
                runtime.release_album_change()
                self._state.active = False
                self._state.phase = "failed"
                self._state.message = "Could not start the album change; try again"
                raise
            return asdict(self._state)

    def progress(self, completed: int, total: int) -> None:
        with self._lock:
            self._state.phase = "checking"
            self._state.completed = completed
            self._state.total = total
            self._state.message = f"Checked {completed} of {total} photos for this frame"

    def _run(self, album_id: str) -> None:
        try:
            with self.configuration.runtime.maintenance_gate.operation():
                message = self.configuration._select_album(album_id, progress=self.progress)
            with self._lock:
                self._state.phase = "complete"
                self._state.message = message + ". The picture on your frame stays unchanged."
        except Exception:
            # Provider errors can contain server response text or credentials.
            # Keep this durable, pollable UI failure specific but safe.
            with self._lock:
                self._state.phase = "failed"
                self._state.message = (
                    "The album could not be changed. Your current album is unchanged. "
                    "Check the photo source and try again."
                )
        finally:
            self.configuration.runtime.release_album_change()
            with self._lock:
                self._state.active = False
