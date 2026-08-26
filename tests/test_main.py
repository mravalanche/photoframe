import pytest

from photoframe.__main__ import server_bind


def test_server_is_localhost_only_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PHOTOFRAME_HOST", raising=False)
    monkeypatch.delenv("PHOTOFRAME_PORT", raising=False)

    assert server_bind() == ("127.0.0.1", 8000)


def test_server_bind_can_be_overridden(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOFRAME_HOST", "0.0.0.0")
    monkeypatch.setenv("PHOTOFRAME_PORT", "8123")

    assert server_bind() == ("0.0.0.0", 8123)


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_server_bind_rejects_invalid_ports(monkeypatch: pytest.MonkeyPatch, port: str):
    monkeypatch.setenv("PHOTOFRAME_PORT", port)

    with pytest.raises(ValueError, match="PHOTOFRAME_PORT"):
        server_bind()
