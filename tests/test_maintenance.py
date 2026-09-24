from threading import Event, Thread

import pytest

from photoframe.updater.maintenance import MaintenanceError, MaintenanceGate


def test_maintenance_gate_drains_and_rejects_new_render_work() -> None:
    gate = MaintenanceGate()
    entered = Event()
    release = Event()

    def render() -> None:
        with gate.operation():
            entered.set()
            release.wait(2)

    worker = Thread(target=render)
    worker.start()
    assert entered.wait(1)
    assert not gate.enter_and_drain(0.01)
    release.set()
    worker.join()
    assert gate.enter_and_drain(0.1)
    with pytest.raises(MaintenanceError), gate.operation():
        pass
    gate.cancel()
    with gate.operation():
        pass
