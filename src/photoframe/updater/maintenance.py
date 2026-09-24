"""A process-wide gate used to drain render work before activation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Condition
from time import monotonic


class MaintenanceError(RuntimeError):
    pass


class MaintenanceGate:
    def __init__(self) -> None:
        self._condition = Condition()
        self._maintenance = False
        self._active = 0

    @property
    def maintenance(self) -> bool:
        with self._condition:
            return self._maintenance

    @contextmanager
    def operation(self) -> Iterator[None]:
        with self._condition:
            if self._maintenance:
                raise MaintenanceError("Photoframe is preparing to restart for an update")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def enter_and_drain(self, timeout: float) -> bool:
        deadline = monotonic() + timeout
        with self._condition:
            self._maintenance = True
            while self._active:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._maintenance = False
                    self._condition.notify_all()
                    return False
                self._condition.wait(remaining)
            return True

    def cancel(self) -> None:
        with self._condition:
            self._maintenance = False
            self._condition.notify_all()
