from __future__ import annotations

import asyncio
import json
import logging
import re
from io import StringIO
from typing import cast

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.correlation import (
    CorrelationIdMiddleware,
    current_correlation_id,
)
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    ObservabilityRouteDependencies,
)
from ci_coordinator.api.http.errors import ResponseCookieCleanupPolicy, UnexpectedErrorMiddleware
from ci_coordinator.observability import (
    ReadinessStatus,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
)

_CORRELATION_ID = re.compile(r"^[0-9a-f]{32}$")


def test_outer_observation_preserves_actual_error_cookie_and_correlation_owners() -> None:
    async def scenario() -> None:
        output = StringIO()
        logger = logging.Logger("outer-error-observation")
        logger.addHandler(logging.StreamHandler(output))

        async def ready() -> ReadinessStatus:
            return ReadinessStatus(True, ())

        app = create_app(
            HttpRouteDependencies(
                observability=ObservabilityRouteDependencies(
                    readiness=ready,
                    metrics=RuntimeMetrics(),
                    request_logger=StructuredEventLogger(logger),
                )
            ),
            include_operator_ui=False,
        )
        for middleware in app.user_middleware:
            if cast(object, middleware.cls) is UnexpectedErrorMiddleware:
                middleware.kwargs["cookie_cleanups"] = (
                    ResponseCookieCleanupPolicy(
                        path="/callback",
                        methods=frozenset({"GET"}),
                        name="transaction",
                        cookie_path="/callback",
                        secure=True,
                        httponly=True,
                        samesite="lax",
                    ),
                )

        seen: list[str | None] = []

        @app.get("/callback")
        async def callback() -> None:
            seen.append(current_correlation_id())
            raise RuntimeError("private callback")

        sent: list[Message] = []

        async def receive() -> Message:
            raise AssertionError("callback does not read")

        async def send(message: Message) -> None:
            sent.append(message)

        assert current_correlation_id() is None
        await app(
            {
                "type": "http",
                "method": "GET",
                "path": "/callback",
                "query_string": b"",
                "headers": [(b"x-correlation-id", b"caller-controlled")],
            },
            receive,
            send,
        )
        assert current_correlation_id() is None
        assert [message["type"] for message in sent] == [
            "http.response.start",
            "http.response.body",
        ]
        assert sent[0]["status"] == 500
        headers = dict(sent[0]["headers"])
        correlation = headers[b"x-correlation-id"].decode()
        assert _CORRELATION_ID.fullmatch(correlation)
        assert seen == [correlation]
        assert headers[b"cache-control"] == b"no-store"
        assert b"transaction=" in headers[b"set-cookie"]
        for flag in (b"Max-Age=0", b"Path=/callback", b"HttpOnly", b"Secure", b"SameSite=lax"):
            assert flag in headers[b"set-cookie"]
        record = json.loads(output.getvalue())
        assert record["correlationId"] == correlation
        assert record["statusCode"] == 500 and record["responseCompleted"] is True
        assert b"private callback" not in sent[1]["body"]
        assert "private callback" not in output.getvalue()

    asyncio.run(scenario())


def test_correlation_identity_is_service_owned_and_context_bound() -> None:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/identity")
    async def identity(request: Request) -> dict[str, str | None]:
        return {
            "context": current_correlation_id(),
            "state": request.state.correlation_id,
        }

    response = TestClient(app).get(
        "/identity",
        headers={"X-Correlation-ID": "caller-controlled"},
    )
    response_identity = response.headers["x-correlation-id"]

    assert _CORRELATION_ID.fullmatch(response_identity)
    assert response_identity != "caller-controlled"
    assert response.json() == {
        "context": response_identity,
        "state": response_identity,
    }
    assert current_correlation_id() is None


def test_every_http_response_gets_a_fresh_correlation_identity() -> None:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    client = TestClient(app)

    first = client.get("/missing")
    second = client.get("/missing")

    assert first.status_code == second.status_code == 404
    assert _CORRELATION_ID.fullmatch(first.headers["x-correlation-id"])
    assert _CORRELATION_ID.fullmatch(second.headers["x-correlation-id"])
    assert first.headers["x-correlation-id"] != second.headers["x-correlation-id"]


def test_redacted_internal_error_preserves_the_outer_correlation_identity() -> None:
    output = StringIO()
    logger = logging.Logger("http-error-diagnostic-test")
    logger.addHandler(logging.StreamHandler(output))
    app = FastAPI()
    app.add_middleware(
        UnexpectedErrorMiddleware,
        diagnostics=RuntimeDiagnosticObserver(StructuredEventLogger(logger)),
    )
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/failure")
    async def failure() -> None:
        raise RuntimeError("secret diagnostic")

    response = TestClient(app, raise_server_exceptions=False).get("/failure")

    assert (response.status_code, response.json()) == (500, {"code": "internal_error"})
    assert _CORRELATION_ID.fullmatch(response.headers["x-correlation-id"])
    assert response.headers["cache-control"] == "no-store"
    assert "secret diagnostic" not in response.text
    diagnostic = json.loads(output.getvalue())
    assert diagnostic["correlationId"] == response.headers["x-correlation-id"]
    assert diagnostic["stage"] == "http_request"
    assert diagnostic["exceptionType"] == "RuntimeError"
    assert "secret diagnostic" not in output.getvalue()
