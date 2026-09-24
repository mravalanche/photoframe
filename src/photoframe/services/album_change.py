"""One album worker, with cancellable preparation and latest-choice replacement."""

from __future__ import annotations

from threading import Lock, Thread
from typing import TYPE_CHECKING

from .album_load import AlbumLoad, AlbumLoadCancelled

if TYPE_CHECKING:
    from .configuration import ConfigurationService


class AlbumChangeJob:
    def __init__(self, configuration: ConfigurationService) -> None:
        self.configuration = configuration
        self._lock = Lock()
        self._latest: AlbumLoad | None = None
        self._current: AlbumLoad | None = None
        self._pending: AlbumLoad | None = None
        self._running = False

    def snapshot(self) -> dict:
        with self._lock:
            return self._latest.snapshot() if self._latest else {"active": False, "phase": "idle"}

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if not self._latest or self._latest.snapshot()["id"] != job_id:
                return False
            if not self._latest.cancel():
                return False
            if self._current:
                self._current.cancel()
            return True

    def start(self, album_id: str, *, refresh: bool = False) -> dict:
        runtime = self.configuration.runtime
        albums, _photos = runtime.catalog_snapshot()
        album = next((item for item in albums if item.id == album_id), None)
        if album is None:
            raise ValueError("Choose an album from the loaded list")
        with self._lock:
            if self._running and self._latest:
                state = self._latest.snapshot()
                if (
                    state["album_id"] == album_id
                    and state["refresh"] == refresh
                    and state["cancellable"]
                ):
                    return state
            if not self._running:
                runtime.claim_album_change()
            if self._current:
                self._current.cancel()
            if self._pending:
                self._pending.cancel()
            runtime.cancel_library_load()
            self._latest = self._pending = AlbumLoad(
                album.id, album.name, waiting=self._running, refresh=refresh
            )
            if not self._running:
                self._running = True
                try:
                    Thread(target=self._run, name="photoframe-album", daemon=True).start()
                except Exception:
                    runtime.release_album_change()
                    self._running = False
                    self._pending = None
                    self._latest.finish("failed", "Could not start the album change; try again")
                    raise
            return self._latest.snapshot()

    def _run(self) -> None:
        while True:
            with self._lock:
                load = self._pending
                self._pending = None
                self._current = load
                if load is None:
                    self.configuration.runtime.release_album_change()
                    self._running = False
                    return
            try:
                with self.configuration.runtime.maintenance_gate.operation():
                    if load.snapshot()["refresh"]:
                        message = self.configuration._refresh_current_album(load)
                    else:
                        message = self.configuration._select_album(
                            str(load.snapshot()["album_id"]), load=load
                        )
                phase = "complete"
                message += ". The picture on your frame stays unchanged."
            except AlbumLoadCancelled:
                phase, message = "cancelled", "Album loading cancelled"
            except Exception:
                phase = "failed"
                message = "The album could not be changed. Your current album is unchanged. Check the photo source and try again."
            with self._lock:
                self._current = None
                finished = self._pending is None
                if finished:
                    self.configuration.runtime.release_album_change()
                    self._running = False
                # Publish terminal state only after releasing the exclusive
                # claim, so clients can immediately start their next action.
                load.finish(phase, message)
                if finished:
                    return
