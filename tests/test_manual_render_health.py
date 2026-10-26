"""A successful manual retry must recover persisted display health."""

from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from photoframe.display import DisplayProfile, InkyDisplay
from photoframe.models import DisplayDriver
from photoframe.renderer import RenderPhase
from photoframe.web import create_app


class RecoverablePanel:
    def __init__(self) -> None:
        self.failed = True

    def set_image(self, image) -> None:
        assert image.size == (1200, 750)

    def show(self, *, busy_wait: bool) -> None:
        assert busy_wait is True
        if self.failed:
            raise OSError("SPI device unavailable")


def wait_for_worker(runtime) -> None:
    deadline = monotonic() + 3
    while runtime.renderer.hardware_busy and monotonic() < deadline:
        Event().wait(0.005)
    assert not runtime.renderer.hardware_busy


@pytest.mark.parametrize("route", ["/render/start", "/photo/next"])
def test_manual_hardware_failure_degrades_health_and_successful_retry_recovers(
    tmp_path: Path, route
):
    app = create_app(tmp_path, demo_mode=True)
    runtime = app.state.runtime
    panel = RecoverablePanel()
    runtime.display = InkyDisplay(
        panel, DisplayProfile(DisplayDriver.INKY, "Test panel", (1200, 750), True, "ok")
    )
    # No lifespan workers: the test controls both manual attempts, while the
    # real render service still prepares images and refreshes off-thread.
    client = TestClient(app, headers={"Origin": "http://testserver"})
    try:
        assert client.get("/health").status_code == 200
        client.post("/photo/preview", data={"photo_id": "coast"})
        response = client.post(route, data={"request_id": "first"})
        assert response.status_code == 200
        wait_for_worker(runtime)
        assert runtime.renderer.snapshot().phase == RenderPhase.FAILED
        failed = client.get("/health")
        assert failed.status_code == 503
        assert failed.json()["checks"]["catalog"]["status"] == "healthy"
        assert failed.json()["checks"]["render"]["status"] == "failed"
        error = runtime.repository.load().refresh_status.last_render_error
        assert error is not None and "SPI device unavailable" in error

        panel.failed = False
        response = client.post(route, data={"request_id": "retry"})
        assert response.status_code == 200
        wait_for_worker(runtime)
        assert runtime.renderer.snapshot().phase == RenderPhase.COMPLETE
        assert runtime.repository.load().refresh_status.last_render_error is None
        recovered = client.get("/health")
        assert recovered.status_code == 200
        assert recovered.json()["checks"]["render"]["status"] == "healthy"
    finally:
        client.close()
