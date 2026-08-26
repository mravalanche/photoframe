from datetime import UTC, datetime, timedelta
from pathlib import Path

from photoframe.cache import PhotoCache
from photoframe.lifecycle import RefreshCoordinator, health_payload
from photoframe.models import AppSettings, Photo
from photoframe.providers import ProviderError


def test_cache_evicts_oldest_file_when_bounded(tmp_path: Path):
    cache = PhotoCache(tmp_path, max_bytes=10)
    cache.put("first", b"123456")
    cache.put("second", b"abcdef")

    assert cache.get("first") is None
    assert cache.get("second") == b"abcdef"
    assert cache.stats().bytes == 6


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

    failed = AppSettings()
    failed.refresh.retry_seconds = 90
    assert RefreshCoordinator(FakeRuntime(fail=True)).run_if_due(failed, current)
    assert failed.refresh_status.next_attempt_at == current + timedelta(seconds=90)
    payload, healthy = health_payload(failed, current)
    assert not healthy and payload["status"] == "degraded"
