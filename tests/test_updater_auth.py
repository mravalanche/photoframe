import os
from pathlib import Path

import pytest

from photoframe.updater.auth import (
    AuthenticationError,
    PinStore,
    RateLimiter,
    SessionStore,
    hash_pin,
    require_same_origin,
    verify_pin,
)


def test_pin_uses_salted_scrypt_and_owner_only_storage(tmp_path: Path) -> None:
    first = hash_pin("correct horse battery staple")
    second = hash_pin("correct horse battery staple")
    assert first != second
    assert verify_pin("correct horse battery staple", first)
    assert not verify_pin("wrong password", first)

    store = PinStore(tmp_path / "update-pin")
    store.establish("correct horse battery staple")
    assert store.verify("correct horse battery staple")
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(AuthenticationError, match="already"):
        store.establish("another secure value")


def test_short_non_ascii_and_malformed_credentials_are_rejected() -> None:
    with pytest.raises(AuthenticationError):
        hash_pin("short")
    with pytest.raises(AuthenticationError):
        hash_pin("long-enough-🔒")
    assert not verify_pin("anything", "malformed")


def test_rate_limit_is_per_identity_and_expires() -> None:
    limiter = RateLimiter(attempts=2, window_seconds=10)
    limiter.fail("client", now=1)
    limiter.fail("client", now=2)
    assert not limiter.allow("client", now=3)
    assert limiter.allow("other", now=3)
    assert limiter.allow("client", now=12)


def test_session_requires_csrf_and_expires() -> None:
    sessions = SessionStore()
    session = sessions.create(now=100)
    sessions.require(session.token, session.csrf, now=101)
    with pytest.raises(AuthenticationError):
        sessions.require(session.token, "wrong", now=101)
    with pytest.raises(AuthenticationError):
        sessions.require(session.token, session.csrf, now=session.expires_at)


def test_origin_must_match_request_endpoint() -> None:
    require_same_origin("https://frame.local:8123", "https", "frame.local:8123")
    with pytest.raises(AuthenticationError):
        require_same_origin("https://evil.example", "https", "frame.local:8123")
    with pytest.raises(AuthenticationError):
        require_same_origin(None, "https", "frame.local:8123")
