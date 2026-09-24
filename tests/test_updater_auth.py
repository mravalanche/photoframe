import pytest

from photoframe.updater.auth import AuthenticationError, SessionStore, require_same_origin


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
