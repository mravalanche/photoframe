from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock, RLock, Thread
from typing import Any

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
        self._state_lock = RLock()
        self._display_lock = Lock()
        self._on_failure: Callable[[str], None] | None = None
        self._timeout_reported = False

    def start(self, photo_id: str, now: datetime | None = None) -> RenderState:
        with self._state_lock:
            if self.state.active or self._display_lock.locked():
                return self.state.model_copy(deep=True)
            self.state = RenderState(
                phase=RenderPhase.PREPARING,
                photo_id=photo_id,
                started_at=now or datetime.now(UTC),
            )
            return self.state.model_copy(deep=True)

    def update(self, settings: DeviceSettings, now: datetime | None = None) -> RenderState:
        timeout_callback: Callable[[str], None] | None = None
        timeout_message: str | None = None
        with self._state_lock:
            state = self.state
            if not state.active or not state.started_at:
                return state.model_copy(deep=True)
            current = now or datetime.now(UTC)
            elapsed = max(0, int((current - state.started_at).total_seconds()))
            state.elapsed_seconds = elapsed
            if self._hardware_refresh:
                if elapsed >= settings.render_timeout_seconds:
                    state.phase = RenderPhase.FAILED
                    state.finished_at = current
                    state.message = "The frame did not complete its refresh before the timeout."
                    if not self._timeout_reported:
                        self._timeout_reported = True
                        timeout_callback = self._on_failure
                        timeout_message = state.message
                result = state.model_copy(deep=True)
            else:
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
                result = state.model_copy(deep=True)
        if timeout_callback and timeout_message:
            timeout_callback(timeout_message)
        return result

    def start_hardware(
        self,
        photo_id: str,
        refresh: Callable[[], None],
        on_complete: Callable[[str], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> RenderState:
        """Run Inky's blocking refresh outside the request thread.

        ``inky.show`` blocks until the panel has refreshed. The worker maps that
        real completion boundary to the existing UI phases, while a timeout
        remains a safety/reporting boundary only.
        """
        with self._state_lock:
            if self.state.active or not self._display_lock.acquire(blocking=False):
                return self.state.model_copy(deep=True)
            self.state = RenderState(
                phase=RenderPhase.SENDING,
                photo_id=photo_id,
                started_at=datetime.now(UTC),
            )
            self._hardware_refresh = True
            self._on_failure = on_failure
            self._timeout_reported = False

        def run() -> None:
            with self._state_lock:
                self.state.phase = RenderPhase.WAITING
            try:
                refresh()
            except Exception as exc:
                message = str(exc)
                with self._state_lock:
                    self.state.phase = RenderPhase.FAILED
                    self.state.finished_at = datetime.now(UTC)
                    self.state.message = message
                    self._hardware_refresh = False
                    self._on_failure = None
                if on_failure:
                    on_failure(message)
            else:
                with self._state_lock:
                    self.state.phase = RenderPhase.COMPLETE
                    self.state.finished_at = datetime.now(UTC)
                    self.state.message = "The frame refresh completed."
                    self.last_rendered_photo_id = photo_id
                    self._hardware_refresh = False
                    self._on_failure = None
                if on_complete:
                    on_complete(photo_id)
            finally:
                # A UI timeout must never release this lock. Only the blocking
                # driver returning (successfully or exceptionally) does so.
                self._display_lock.release()

        Thread(target=run, name="photoframe-inky", daemon=True).start()
        return self.snapshot()

    def reset(self) -> None:
        with self._state_lock:
            if self._display_lock.locked():
                return
            self.state = RenderState()
            self._hardware_refresh = False
            self._on_failure = None
            self._timeout_reported = False

    @property
    def hardware_busy(self) -> bool:
        return self._display_lock.locked()

    def snapshot(self) -> RenderState:
        with self._state_lock:
            return self.state.model_copy(deep=True)

    def rendered_photo_id(self) -> str | None:
        with self._state_lock:
            return self.last_rendered_photo_id


class RenderService:
    """One entry point shared by UI and autonomous scheduling."""

    def __init__(
        self,
        coordinator: MockRenderCoordinator,
        prepare: Callable[[str], Any],
        refresh: Callable[[Any], None] | None,
    ) -> None:
        self.coordinator = coordinator
        self.prepare = prepare
        self.refresh = refresh
        self._start_lock = Lock()

    def start(
        self,
        photo_id: str,
        on_complete: Callable[[str], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> RenderState:
        with self._start_lock:
            state = self.coordinator.snapshot()
            if state.active or self.coordinator.hardware_busy:
                return state
            prepared = self.prepare(photo_id)
            refresh = self.refresh
            if refresh is None:
                return self.coordinator.start(photo_id)
            return self.coordinator.start_hardware(
                photo_id,
                lambda: refresh(prepared),
                on_complete=on_complete,
                on_failure=on_failure,
            )
