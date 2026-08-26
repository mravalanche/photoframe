from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock, Thread

from pydantic import BaseModel

from .models import DeviceSettings


class RenderPhase(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    SENDING = "sending"
    WAITING = "waiting"
    COMPLETE = "complete"
    FAILED = "failed"


class RenderState(BaseModel):
    phase: RenderPhase = RenderPhase.IDLE
    photo_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: int = 0
    message: str | None = None

    @property
    def active(self) -> bool:
        return self.phase in {
            RenderPhase.PREPARING,
            RenderPhase.SENDING,
            RenderPhase.WAITING,
        }


class MockRenderCoordinator:
    """Time-backed simulator matching the future Inky driver's observable phases.

    The hardware implementation will replace this class and call
    ``display.show(busy_wait=True)`` during WAITING. That call returns only after
    the display busy signal clears; the timeout is a safety boundary, not a
    percentage estimate.
    """

    prepare_seconds = 1
    send_seconds = 1

    def __init__(self) -> None:
        self.state = RenderState()
        self.last_rendered_photo_id: str | None = None
        self._hardware_refresh = False
        self._lock = Lock()

    def start(self, photo_id: str, now: datetime | None = None) -> RenderState:
        if self.state.active:
            return self.state
        self.state = RenderState(
            phase=RenderPhase.PREPARING,
            photo_id=photo_id,
            started_at=now or datetime.now(UTC),
        )
        return self.state

    def update(self, settings: DeviceSettings, now: datetime | None = None) -> RenderState:
        state = self.state
        if not state.active or not state.started_at:
            return state
        current = now or datetime.now(UTC)
        elapsed = max(0, int((current - state.started_at).total_seconds()))
        state.elapsed_seconds = elapsed
        if self._hardware_refresh:
            if elapsed >= settings.render_timeout_seconds:
                state.phase = RenderPhase.FAILED
                state.finished_at = current
                state.message = "The frame did not complete its refresh before the timeout."
                self._hardware_refresh = False
            return state
        if elapsed >= settings.render_timeout_seconds:
            state.phase = RenderPhase.FAILED
            state.finished_at = current
            state.message = "The frame did not clear its busy signal before the timeout."
        elif elapsed < self.prepare_seconds:
            state.phase = RenderPhase.PREPARING
        elif elapsed < self.prepare_seconds + self.send_seconds:
            state.phase = RenderPhase.SENDING
        elif elapsed < settings.expected_refresh_seconds:
            state.phase = RenderPhase.WAITING
        else:
            state.phase = RenderPhase.COMPLETE
            state.finished_at = current
            state.message = "The frame refresh completed."
            self.last_rendered_photo_id = state.photo_id
        return state

    def start_hardware(self, photo_id: str, refresh: Callable[[], None]) -> RenderState:
        """Run Inky's blocking refresh outside the request thread.

        ``inky.show`` blocks until the panel has refreshed. The worker maps that
        real completion boundary to the existing UI phases, while a timeout
        remains a safety/reporting boundary only.
        """
        if self.state.active:
            return self.state
        self.state = RenderState(
            phase=RenderPhase.SENDING,
            photo_id=photo_id,
            started_at=datetime.now(UTC),
        )
        self._hardware_refresh = True

        def run() -> None:
            with self._lock:
                if not self._hardware_refresh:
                    return
                self.state.phase = RenderPhase.WAITING
            try:
                refresh()
            except RuntimeError as exc:
                with self._lock:
                    self.state.phase = RenderPhase.FAILED
                    self.state.finished_at = datetime.now(UTC)
                    self.state.message = str(exc)
                    self._hardware_refresh = False
                return
            with self._lock:
                if self._hardware_refresh:
                    self.state.phase = RenderPhase.COMPLETE
                    self.state.finished_at = datetime.now(UTC)
                    self.state.message = "The frame refresh completed."
                    self.last_rendered_photo_id = photo_id
                    self._hardware_refresh = False

        Thread(target=run, name="photoframe-inky", daemon=True).start()
        return self.state

    def reset(self) -> None:
        self.state = RenderState()
        self._hardware_refresh = False
