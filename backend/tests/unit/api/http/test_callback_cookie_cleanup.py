from __future__ import annotations

import asyncio
from typing import Protocol

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from ci_coordinator.api.http.errors import (
    ResponseCookieCleanupPolicy,
    UnexpectedErrorMiddleware,
)
from ci_coordinator.api.http.public_request_limits import (
    PublicRequestLimitMiddleware,
    PublicRequestLimitPolicy,
)
from ci_coordinator.api.http.request_admission import (
    RequestAdmissionMiddleware,
    RequestAdmissionPolicy,
)

_CALLBACK_PATH = "/oauth/callback"
_COOKIE_NAME = "__Host-callback"
_CLEANUP = ResponseCookieCleanupPolicy(
    path=_CALLBACK_PATH,
    methods=frozenset({"GET"}),
    name=_COOKIE_NAME,
    cookie_path=_CALLBACK_PATH,
    secure=True,
    httponly=True,
    samesite="lax",
)


class _HeaderCollection(Protocol):
    def get_list(self, key: str) -> list[str]: ...


def test_callback_cookie_cleanup_precedes_public_rate_rejection() -> None:
    app = FastAPI()
    app.add_middleware(
        PublicRequestLimitMiddleware,
        policies=(
            PublicRequestLimitPolicy(
                path=_CALLBACK_PATH,
                methods=frozenset({"GET"}),
                burst=1,
                refill_rate_per_second=1.0,
                rejection_response={"ok": False, "error": "rate_limited"},
            ),
        ),
        clock=lambda: 0.0,
    )
    app.add_middleware(UnexpectedErrorMiddleware, cookie_cleanups=(_CLEANUP,))

    @app.get(_CALLBACK_PATH)
    async def callback() -> Response:
        return Response(status_code=204)

    client = TestClient(app, base_url="https://coordinator.example")
    assert client.get(_CALLBACK_PATH).status_code == 204
    rejected = client.get(_CALLBACK_PATH)

    assert rejected.status_code == 429
    _assert_one_cleanup(rejected.headers)


def test_callback_cookie_cleanup_precedes_request_deadline() -> None:
    app = FastAPI()
    policy = RequestAdmissionPolicy(
        path=_CALLBACK_PATH,
        methods=frozenset({"GET"}),
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    )
    app.add_middleware(
        RequestAdmissionMiddleware,
        timeout_seconds=1,
        policies=(policy,),
        liveness_paths=frozenset(),
        default_timeout_response={"ok": False, "error": "unavailable"},
    )
    app.add_middleware(UnexpectedErrorMiddleware, cookie_cleanups=(_CLEANUP,))

    @app.get(_CALLBACK_PATH)
    async def callback() -> Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    response = TestClient(app, base_url="https://coordinator.example").get(_CALLBACK_PATH)

    assert response.status_code == 503
    _assert_one_cleanup(response.headers)


def _assert_one_cleanup(headers: _HeaderCollection) -> None:
    values = [value for value in headers.get_list("set-cookie") if value.startswith(_COOKIE_NAME)]
    assert len(values) == 1
    assert "Max-Age=0" in values[0]
    assert f"Path={_CALLBACK_PATH}" in values[0]
