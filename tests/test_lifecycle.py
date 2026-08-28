from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Thread

from photoframe.cache import PhotoCache
from photoframe.lifecycle import RefreshCoordinator, RefreshWorker, health_payload
from photoframe.models import AppSettings, Photo
from photoframe.providers import ProviderError


def test_cache_evicts_oldest_file_when_bounded(tmp_path: Path):
    cache = PhotoCache(tmp_path, max_bytes=10)
    cache.put("first", b"123456")
    cache.put("second", b"abcdef")

    assert cache.get("first") is None
    assert cache.get("second") == b"abcdef"
    assert cache.stats().bytes == 6


def test_concurrent_cache_writes_remain_bounded_and_intact(tmp_path: Path):
    cache = PhotoCache(tmp_path, max_bytes=10)
    start = Barrier(3)

    def write(key: str, value: bytes):
        start.wait()
        cache.put(key, value)

    threads = [
        Thread(target=write, args=("one", b"111111")),
        Thread(target=write, args=("two", b"222222")),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()
    assert cache.stats().bytes <= 10
    surviving = [value for value in (cache.get("one"), cache.get("two")) if value is not None]
    assert surviving in ([b"111111"], [b"222222"])


def test_cache_reset_removes_current_and_interrupted_reset_data(tmp_path: Path):
    cache = PhotoCache(tmp_path, max_bytes=100)
    cache.put("current", b"current")
    interrupted = tmp_path / ".photo-cache.reset-interrupted"
    interrupted.mkdir()
    (interrupted / "orphan.image").write_bytes(b"orphan")

    cache.clear()

    assert cache.stats().files == 0
    assert not interrupted.exists()


def test_cache_persists_decodability_without_counting_marker_as_an_image(tmp_path: Path):
    cache = PhotoCache(tmp_path, max_bytes=100)

    assert cache.decodability("asset") is None
    cache.set_decodability("asset", False)

    restarted = PhotoCache(tmp_path, max_bytes=100)
    assert restarted.decodability("asset") is False
    assert restarted.stats().files == 0


class FakeRuntime:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.cached: list[str] = []

    def refresh_photos(self):
        if self.fail:
            raise ProviderError("offline")
        return [Photo(id="wide", filename="wide.jpg", width=1200, height=800)]

    def cache_photo(self, photo_id):
        self.cached.append(photo_id)
        return b"image"

    def renderable_photos(self, photos, frame):
        return [photo for photo in photos if photo.matches(frame.orientation)]

    def cache_stats(self):
        from photoframe.cache import CacheStats

        return CacheStats(len(self.cached), len(self.cached) * 5)


def test_refresh_prefetches_and_tracks_retry_health():
    current = datetime(2026, 1, 1, tzinfo=UTC)
    settings = AppSettings()
    settings.refresh.catalog_refresh_seconds = 600
    runtime = FakeRuntime()
    coordinator = RefreshCoordinator(runtime)

    assert coordinator.run_if_due(settings, current)
    assert runtime.cached == ["wide"]
    assert settings.refresh_status.next_attempt_at == current + timedelta(seconds=600)
    payload, healthy = health_payload(settings, current)
    assert healthy and payload["status"] == "healthy"

    settings.refresh_status.consecutive_failures = 1
    settings.refresh_status.last_error = "offline again"
    payload, healthy = health_payload(settings, current)
    assert not healthy and payload["detail"] == "offline again"
    settings.refresh_status.consecutive_failures = 0
    settings.refresh_status.last_error = None
    payload, healthy = health_payload(settings, current)
    assert healthy

    settings.refresh_status.last_render_error = "Frame refresh timed out"
    payload, healthy = health_payload(settings, current)
    assert not healthy
    assert payload["detail"] == "Frame refresh timed out"
    settings.refresh_status.last_render_error = None
    payload, healthy = health_payload(settings, current)
    assert healthy

    failed = AppSettings()
    failed.refresh.retry_seconds = 90
    assert RefreshCoordinator(FakeRuntime(fail=True)).run_if_due(failed, current)
    assert failed.refresh_status.next_attempt_at == current + timedelta(seconds=90)
    payload, healthy = health_payload(failed, current)
    assert not healthy and payload["status"] == "degraded"


def test_refresh_worker_survives_unexpected_exception():
    attempted = Event()
    recorded = Event()

    def fail():
        attempted.set()
        raise ValueError("unexpected")

    worker = RefreshWorker(fail, lambda _exc: recorded.set())
    worker.start()
    assert attempted.wait(1)
    assert recorded.wait(1)
    assert worker.thread.is_alive()
    worker.stop()
