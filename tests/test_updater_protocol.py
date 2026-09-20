import json

import pytest

from photoframe.updater.protocol import ProtocolError, Request


def request(**changes: object) -> bytes:
    value: dict[str, object] = {
        "protocol": 1,
        "action": "status",
        "token": "secret",
        "request_id": "a" * 32,
        "release": None,
    }
    value.update(changes)
    return json.dumps(value).encode()


def test_protocol_accepts_only_fixed_authenticated_operations() -> None:
    assert Request.parse(request(), "secret").action == "status"
    assert Request.parse(request(action="stage", release="1.3.0"), "secret").release == "1.3.0"


@pytest.mark.parametrize(
    "changes",
    [
        {"token": "wrong"},
        {"action": "shell"},
        {"action": "stage", "release": "../main"},
        {"action": "activate", "release": None},
        {"action": "rollback", "release": "1.2.1"},
        {"request_id": "not-a-job"},
        {"url": "https://evil.example/payload"},
    ],
)
def test_protocol_rejects_ambient_authority(changes: dict[str, object]) -> None:
    with pytest.raises(ProtocolError):
        Request.parse(request(**changes), "secret")
