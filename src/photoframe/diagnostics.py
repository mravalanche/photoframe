"""Small persistent operational history, deliberately excluding arbitrary log payloads."""

from __future__ import annotations

import errno
import json
import re
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

import httpx

from .persistence import atomic_write

MAX_EVENTS = 500
MAX_FILE_BYTES = 256 * 1024
EVENTS: dict[str, tuple[str, str]] = {
    "app.started": ("info", "Photoframe started"),
    "app.stopped": ("info", "Photoframe stopped normally"),
    "catalog.refreshed": ("info", "Photo catalog refreshed"),
    "catalog.failed": ("error", "Photo catalog refresh failed"),
    "worker.failed": ("error", "Background refresh worker failed; it will try again"),
    "display.ready": ("info", "Physical display detected"),
    "display.unavailable": ("warning", "Physical display unavailable; check hardware settings"),
    "display.completed": ("info", "Frame update completed"),
    "display.failed": ("error", "Frame update failed"),
    "provider.connected": ("info", "Photo provider connection verified"),
    "provider.failed": ("error", "Photo provider connection failed"),
    "request.failed": ("error", "An unexpected application request failed"),
}
DETAILS = {
    "timeout": "The operation timed out. Check connectivity or display responsiveness.",
    "connect": "Could not connect to the photo provider. Check Wi-Fi, DNS and the server.",
    "network": "The network connection failed during a photo provider request.",
    "unreachable": "Could not reach the photo provider. Check Wi-Fi, DNS and the server.",
    "limit": "The photo source exceeded a configured safety limit.",
    "decode": "The photo could not be decoded. Check its format and file integrity.",
    "display": "Display operation failed. Check the panel connection and hardware settings.",
    "busy": "The requested operation was busy. Try again after the current operation finishes.",
    "unknown": "The operation failed. System service logs may contain further technical details.",
}


def _historic_detail(value: str) -> str:
    if not value or value in DETAILS.values():
        return value
    if re.fullmatch(r"Photo provider returned HTTP [1-5][0-9]{2}\.", value):
        return value
    if (
        match := re.fullmatch(
            r"Operating system error: ([A-Z0-9]+)\. Check storage, permissions or hardware\.", value
        )
    ) and match[1] in errno.errorcode.values():
        return value
    return DETAILS["unknown"]


def failure_detail(error: BaseException | str) -> str:
    """Classify failures with fixed vocabulary; never persist exception messages.

    Provider errors may contain credentials, URLs or remote response bodies. Inspect
    their cause chain for useful transport/status information, then discard payloads.
    Render callbacks only supply strings, so recognise a small safe vocabulary there.
    """
    current = error if isinstance(error, BaseException) else None
    seen: set[int] = set()
    messages = [error[:2000]] if isinstance(error, str) else []
    while current is not None and id(current) not in seen and len(seen) < 16:
        seen.add(id(current))
        messages.append(str(current)[:2000])
        if isinstance(current, httpx.HTTPStatusError):
            return f"Photo provider returned HTTP {current.response.status_code}."
        if isinstance(current, (httpx.TimeoutException, TimeoutError)):
            return DETAILS["timeout"]
        if isinstance(current, httpx.ConnectError):
            return DETAILS["connect"]
        if isinstance(current, httpx.NetworkError):
            return DETAILS["network"]
        if isinstance(current, OSError) and current.errno is not None:
            code = errno.errorcode.get(current.errno)
            if code is not None:
                return f"Operating system error: {code}. Check storage, permissions or hardware."
        current = current.__cause__ or current.__context__
    # Only return literal classifications; no captured text reaches the log.
    message = " ".join(messages).lower()
    if re.search(r"\b(timeout|timed out|too long)\b", message):
        return DETAILS["timeout"]
    if match := re.search(r"\bimmich returned ([1-5][0-9]{2})\b", message):
        return f"Photo provider returned HTTP {match[1]}."
    if "could not reach immich" in message:
        return DETAILS["unreachable"]
    if "safe" in message and "limit" in message:
        return DETAILS["limit"]
    if any(word in message for word in ("decode", "image format", "image data")):
        return DETAILS["decode"]
    if "display" in message or "hardware" in message or "spi" in message:
        return DETAILS["display"]
    if "busy" in message:
        return DETAILS["busy"]
    return DETAILS["unknown"]


class DiagnosticLog:
    """One app process writes a bounded atomic history; logging never breaks a job."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "diagnostics.json"
        self._lock = RLock()
        self._events: deque[dict[str, str]] = deque(maxlen=MAX_EVENTS)
        self.storage_error = False
        try:
            with self.path.open("rb") as handle:
                payload = handle.read(MAX_FILE_BYTES + 1)
            if len(payload) > MAX_FILE_BYTES:
                raise ValueError("Diagnostic history too large")
            entries = json.loads(payload)
            if not isinstance(entries, list):
                raise ValueError("Invalid diagnostic history")
            for entry in entries[-MAX_EVENTS:]:
                if not isinstance(entry, dict) or entry.get("code") not in EVENTS:
                    continue
                # Rebuild labels and constrain historic data before displaying it.
                code = entry["code"]
                timestamp = datetime.fromisoformat(entry["timestamp"]).astimezone(UTC).isoformat()
                detail = entry.get("detail", "")
                if not isinstance(detail, str) or len(detail) > 200:
                    continue
                self._events.append(
                    {"timestamp": timestamp, "code": code, "detail": _historic_detail(detail)}
                )
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self.storage_error = True

    def record(self, code: str, error: BaseException | str | None = None) -> None:
        if code not in EVENTS:
            raise ValueError("Unknown diagnostic event")
        with self._lock:
            self._events.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                    "code": code,
                    "detail": failure_detail(error) if error is not None else "",
                }
            )
            try:
                atomic_write(
                    self.path,
                    json.dumps(list(self._events), ensure_ascii=True).encode(),
                    mode=0o600,
                )
                self.storage_error = False
            except OSError:
                self.storage_error = True

    def recent(self, *, failures_only: bool = False, limit: int = 100) -> list[dict[str, str]]:
        with self._lock:
            events = [
                {**event, "level": EVENTS[event["code"]][0], "message": EVENTS[event["code"]][1]}
                for event in reversed(self._events)
                if not failures_only or EVENTS[event["code"]][0] != "info"
            ]
        return events[: max(0, min(limit, MAX_EVENTS))]

    def text(self) -> str:
        return (
            "Photoframe operational history (UTC, newest first)\n"
            + "\n".join(
                f"{event['timestamp']} {event['level'].upper()} {event['code']}: "
                f"{event['message']} {event['detail']}".rstrip()
                for event in self.recent(limit=MAX_EVENTS)
            )
            + "\n"
        )
