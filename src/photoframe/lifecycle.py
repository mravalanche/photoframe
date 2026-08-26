"""Unattended catalog refresh, local prefetching, and retry bookkeeping."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Protocol

from .cache import CacheStats
from .models import AppSettings, Photo
from .providers import ProviderError


class RefreshRuntime(Protocol):
    def refresh_photos(self) -> list[Photo]: ...
    def cache_photo(self, photo_id: str) -> bytes: ...
    def cache_stats(self) -> CacheStats: ...


class RefreshCoordinator:
    """Does one bounded refresh attempt when due; safe to call frequently."""

    def __init__(self, runtime: RefreshRuntime) -> None:
        self.runtime = runtime

    @staticmethod
    def due(settings: AppSettings, now: datetime) -> bool:
        next_attempt = settings.refresh_status.next_attempt_at
        return next_attempt is None or now >= next_attempt

    def run_if_due(self, settings: AppSettings, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if not self.due(settings, current):
            return False
        status, policy = settings.refresh_status, settings.refresh
        status.last_attempt_at = current
        try:
            photos = self.runtime.refresh_photos()
            eligible = [photo for photo in photos if photo.matches(settings.frame.orientation)]
            # The first candidates are deterministic, making prefetch bounded
            # and independent of a particular provider's ordering quirks.
            for photo in eligible[: policy.cache_prefetch_count]:
                self.runtime.cache_photo(photo.id)
            cache = self.runtime.cache_stats()
            status.last_success_at = current
            status.next_attempt_at = current + timedelta(seconds=policy.catalog_refresh_seconds)
            status.consecutive_failures = 0
            status.last_error = None
            status.cached_photo_count, status.cached_bytes = cache.files, cache.bytes
            return True
        except (ProviderError, RuntimeError, OSError) as exc:
            status.consecutive_failures += 1
            status.last_error = str(exc)[:500]
            status.next_attempt_at = current + timedelta(seconds=policy.retry_seconds)
            cache = self.runtime.cache_stats()
            status.cached_photo_count, status.cached_bytes = cache.files, cache.bytes
            return True


def health_payload(
    settings: AppSettings, now: datetime | None = None
) -> tuple[dict[str, object], bool]:
    """Return a monitor-friendly body plus whether it is healthy (HTTP 200)."""
    current = now or datetime.now(UTC)
    status = settings.refresh_status
    age = (
        int((current - status.last_success_at).total_seconds()) if status.last_success_at else None
    )
    healthy = (
        status.last_success_at is not None
        and age is not None
        and age <= settings.refresh.health_stale_seconds
        and status.consecutive_failures == 0
        and status.last_render_error is None
    )
    state = "healthy" if healthy else "degraded"
    return (
        {
            "status": state,
            "last_success_at": status.last_success_at,
            "last_attempt_at": status.last_attempt_at,
            "next_attempt_at": status.next_attempt_at,
            "consecutive_failures": status.consecutive_failures,
            "cached_photo_count": status.cached_photo_count,
            "cached_bytes": status.cached_bytes,
            "detail": (
                status.last_render_error or status.last_error
                if not healthy
                else "Refresh lifecycle is current"
            ),
        },
        healthy,
    )


class RefreshWorker:
    """Small daemon worker; systemd remains responsible for process recovery."""

    def __init__(
        self,
        run_once: Callable[[], bool],
        on_unexpected: Callable[[Exception], None] | None = None,
    ) -> None:
        self.run_once = run_once
        self.on_unexpected = on_unexpected
        self.stop_event = Event()
        self.thread = Thread(target=self._run, name="photoframe-refresh", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # keep the daemon alive after integration faults
                if self.on_unexpected:
                    with suppress(Exception):
                        self.on_unexpected(exc)
            # A short polling interval ensures a configured retry happens near
            # its due time without keeping a request thread alive.
            self.stop_event.wait(15)
