"""Authenticated browser endpoints for appliance updates."""

import json
import os
from pathlib import Path
from uuid import UUID

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .. import __version__
from ..lifecycle import RefreshWorker
from ..updater.auth import (
    AuthenticationError,
    PinStore,
    RateLimiter,
    SessionStore,
    require_same_origin,
)
from ..updater.controller import UpdateController


def register_updates(app, templates, runtime, data_dir):
    controller = UpdateController(data_dir, runtime)
    app.state.updater = controller
    sessions, limiter = SessionStore(), RateLimiter()
    pin = PinStore(Path(os.getenv("PHOTOFRAME_UPDATE_PIN_FILE", "/etc/photoframe/update-pin.hash")))
    worker = RefreshWorker(controller.tick)
    app.router.add_event_handler("startup", worker.start)
    app.router.add_event_handler("shutdown", worker.stop)

    @app.get("/health/update")
    def update_health():
        return {"version": __version__, "ready": True}

    @app.get("/updates")
    def updates(request: Request):
        return templates.TemplateResponse(
            request=request, name="updates.html", context={"version": __version__}
        )

    @app.get("/api/updates/status")
    def status():
        return JSONResponse(controller.status(), headers={"Cache-Control": "no-store"})

    @app.get("/api/updates/releases")
    def public_releases():
        return JSONResponse(
            controller.check_public_release(), headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/updates/{action}")
    async def mutate(action: str, request: Request):
        try:
            require_same_origin(
                request.headers.get("origin"), request.url.scheme, request.headers.get("host", "")
            )
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise AuthenticationError("JSON requests are required")
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError("Update request is too large")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Invalid request")
            if action == "login":
                identity = request.client.host if request.client else "unknown"
                if not limiter.allow(identity) or not limiter.allow("global"):
                    return JSONResponse(
                        {"message": "Too many attempts. Try again in five minutes."},
                        status_code=429,
                    )
                value = body.get("pin", "")
                if (
                    not isinstance(value, str)
                    or not 8 <= len(value) <= 128
                    or not pin.verify(value)
                ):
                    limiter.fail(identity)
                    limiter.fail("global")
                    raise AuthenticationError("Incorrect update PIN")
                limiter.clear(identity)
                session = sessions.create()
                response = JSONResponse({"csrf": session.csrf})
                response.set_cookie(
                    "photoframe_update",
                    session.token,
                    max_age=900,
                    httponly=True,
                    secure=request.url.scheme == "https",
                    samesite="strict",
                    path="/api/updates",
                )
                return response
            sessions.require(
                request.cookies.get("photoframe_update"), request.headers.get("x-csrf-token")
            )
            if action == "logout":
                sessions.revoke(request.cookies.get("photoframe_update"))
                response = JSONResponse({"message": "Update controls locked"})
                response.delete_cookie("photoframe_update", path="/api/updates")
                return response
            if action == "preferences":
                if type(body.get("weekly")) is not bool:
                    raise ValueError("Weekly checks must be on or off")
                return await run_in_threadpool(controller.configure, body["weekly"])
            if action not in {"check", "stage", "activate", "rollback"}:
                raise ValueError("Unknown update action")
            if action in {"activate", "rollback"} and body.get("confirmed") is not True:
                raise ValueError("Confirm Apply & restart before continuing")
            request_id = body.get("request_id")
            if request_id is not None:
                if not isinstance(request_id, str):
                    raise ValueError("Request ID must be a UUID string")
                request_id = UUID(request_id).hex
            return await run_in_threadpool(
                controller.action, action, body.get("release"), request_id
            )
        except AuthenticationError as exc:
            return JSONResponse({"message": str(exc)}, status_code=403)
        except (OSError, ValueError, RuntimeError) as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
