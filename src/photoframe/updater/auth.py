"""Update administrator credentials and short-lived browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import stat
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from ..persistence import atomic_write

PIN_MIN_LENGTH = 8
PIN_MAX_LENGTH = 128
SESSION_SECONDS = 15 * 60
PRIVATE_MODE = stat.S_IRUSR | stat.S_IWUSR


class AuthenticationError(ValueError):
    pass


def hash_pin(pin: str, *, salt: bytes | None = None) -> str:
    if not PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH or not pin.isascii():
        raise AuthenticationError("update PIN must be 8 to 128 ASCII characters")
    actual_salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        pin.encode(), salt=actual_salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024**2
    )
    return f"scrypt$32768$8$1${base64.b64encode(actual_salt).decode()}${base64.b64encode(derived).decode()}"


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (n, r, p) != ("32768", "8", "1") or len(pin) > PIN_MAX_LENGTH:
            return False
        derived = hashlib.scrypt(
            pin.encode(),
            salt=base64.b64decode(salt, validate=True),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
            maxmem=64 * 1024**2,
        )
        return hmac.compare_digest(derived, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


class PinStore:
    def __init__(self, path: Path):
        self.path = path

    def establish(self, pin: str) -> None:
        if self.path.exists():
            raise AuthenticationError("update PIN is already established")
        atomic_write(self.path, (hash_pin(pin) + "\n").encode(), mode=PRIVATE_MODE)

    def verify(self, pin: str) -> bool:
        try:
            return verify_pin(pin, self.path.read_text().strip())
        except OSError:
            return False


class RateLimiter:
    def __init__(self, attempts: int = 5, window_seconds: int = 300):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, identity: str, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        failures = self._failures[identity]
        while failures and failures[0] <= current - self.window_seconds:
            failures.popleft()
        return len(failures) < self.attempts

    def fail(self, identity: str, now: float | None = None) -> None:
        self._failures[identity].append(time.monotonic() if now is None else now)

    def clear(self, identity: str) -> None:
        self._failures.pop(identity, None)


@dataclass(frozen=True)
class UpdateSession:
    token: str
    csrf: str
    expires_at: float


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, UpdateSession] = {}

    def create(self, now: float | None = None) -> UpdateSession:
        current = time.time() if now is None else now
        session = UpdateSession(
            secrets.token_urlsafe(32), secrets.token_urlsafe(32), current + SESSION_SECONDS
        )
        self._sessions = {
            key: value for key, value in self._sessions.items() if value.expires_at > current
        }
        if len(self._sessions) >= 100:
            self._sessions.pop(next(iter(self._sessions)))
        self._sessions[session.token] = session
        return session

    def require(self, token: str | None, csrf: str | None, now: float | None = None) -> None:
        current = time.time() if now is None else now
        session = self._sessions.get(token or "")
        if (
            session is None
            or session.expires_at <= current
            or not csrf
            or not hmac.compare_digest(session.csrf.encode(), csrf.encode())
        ):
            raise AuthenticationError("update authorization is missing or expired")

    def revoke(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)


def require_same_origin(origin: str | None, scheme: str, host: str) -> None:
    expected = f"{scheme}://{host}"
    if origin is None or not hmac.compare_digest(
        origin.rstrip("/").encode(), expected.rstrip("/").encode()
    ):
        raise AuthenticationError("request origin is not allowed")


def write_bootstrap_credential(path: Path, pin: str) -> None:
    """Installer-facing helper that never returns or logs the credential."""
    atomic_write(path, (hash_pin(pin) + "\n").encode(), mode=PRIVATE_MODE)
