"""Short-lived browser sessions for same-origin update request protection.

These sessions provide CSRF protection, not user authentication.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass

SESSION_SECONDS = 15 * 60


class AuthenticationError(ValueError):
    pass


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
            raise AuthenticationError("update browser session is missing or expired")

    def revoke(self, token: str | None) -> None:
        self._sessions.pop(token or "", None)


def require_same_origin(origin: str | None, scheme: str, host: str) -> None:
    expected = f"{scheme}://{host}"
    if origin is None or not hmac.compare_digest(
        origin.rstrip("/").encode(), expected.rstrip("/").encode()
    ):
        raise AuthenticationError("request origin is not allowed")
