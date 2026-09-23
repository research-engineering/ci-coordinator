from __future__ import annotations

import json
import logging
import re
from io import StringIO

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ci_coordinator.api.http.correlation import (
    CorrelationIdMiddleware,
    current_correlation_id,
)
from ci_coordinator.api.http.errors import UnexpectedErrorMiddleware
from ci_coordinator.observability import RuntimeDiagnosticObserver, StructuredEventLogger

_CORRELATION_ID = re.compile(r"^[0-9a-f]{32}$")


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
