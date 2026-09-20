"""Fixed, versioned protocol between the web process and root updater helper."""

from __future__ import annotations

import hmac
import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MAX_MESSAGE_BYTES = 64 * 1024
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
Action = Literal["status", "check", "stage", "activate", "rollback"]


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    protocol: int
    action: Action
    token: str
    request_id: str
    release: str | None = None

    @classmethod
    def parse(cls, raw: bytes, expected_token: str) -> Request:
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ProtocolError("request is too large")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError("request is not valid JSON") from exc
        if not isinstance(value, dict) or not set(value) <= {
            "protocol",
            "action",
            "token",
            "request_id",
            "release",
        }:
            raise ProtocolError("request fields are invalid")
        try:
            request = cls(**value)
        except TypeError as exc:
            raise ProtocolError("request fields are invalid") from exc
        if (
            type(request.protocol) is not int
            or not isinstance(request.action, str)
            or not isinstance(request.token, str)
            or not isinstance(request.request_id, str)
            or (request.release is not None and not isinstance(request.release, str))
        ):
            raise ProtocolError("request fields have invalid types")
        if request.protocol != 1 or request.action not in {
            "status",
            "check",
            "stage",
            "activate",
            "rollback",
        }:
            raise ProtocolError("unsupported protocol action")
        if not hmac.compare_digest(request.token.encode(), expected_token.encode()):
            raise ProtocolError("helper authentication failed")
        if not _JOB_ID.fullmatch(request.request_id):
            raise ProtocolError("request ID is invalid")
        needs_release = request.action in {"stage", "activate"}
        if needs_release != (request.release is not None):
            raise ProtocolError("release ID is required only for stage and activate")
        if request.release is not None and not _VERSION.fullmatch(request.release):
            raise ProtocolError("release ID is invalid")
        return request


class HelperClient:
    def __init__(self, socket_path: Path, token_path: Path, timeout: float = 15.0):
        self.socket_path = socket_path
        self.token_path = token_path
        self.timeout = timeout

    def call(self, action: Action, request_id: str, release: str | None = None) -> dict[str, Any]:
        token = self.token_path.read_text().strip()
        request = Request(1, action, token, request_id, release)
        payload = json.dumps(request.__dict__, separators=(",", ":")).encode() + b"\n"
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ProtocolError("request is too large")
        with socket.socket(getattr(socket, "AF_UNIX"), socket.SOCK_STREAM) as connection:  # noqa: B009
            connection.settimeout(self.timeout)
            connection.connect(str(self.socket_path))
            connection.sendall(payload)
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > MAX_MESSAGE_BYTES:
                    raise ProtocolError("helper response is too large")
        try:
            result = json.loads(response)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError("helper returned an invalid response") from exc
        if not isinstance(result, dict):
            raise ProtocolError("helper returned an invalid response")
        return result
