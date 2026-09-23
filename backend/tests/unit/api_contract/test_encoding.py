from __future__ import annotations

import httpx2
import pytest
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException

from ci_coordinator.api.http.errors import framework_http_error

from .harness import Harness
from .oracles import assert_no_effects
from .profile import HEADERS, VALIDATION_PATH


@pytest.mark.parametrize("body", [b"\xff", b'{"source":"\xc3"}', b"\x80\x81"])
async def test_invalid_json_encoding_is_typed_before_any_capability_effect(
    harness: Harness, body: bytes
) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=harness.app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            VALIDATION_PATH, content=body, headers={**HEADERS, "Content-Type": "application/json"}
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-correlation-id"]
    assert_no_effects(harness.ports)


@pytest.mark.parametrize(
    "status,cause",
    [
        (400, None),
        (400, ValueError("internal")),
        (409, UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")),
    ],
)
async def test_unrelated_framework_http_errors_keep_their_contract(
    status: int, cause: Exception | None
) -> None:
    app = FastAPI()
    app.add_exception_handler(HTTPException, framework_http_error)

    @app.get("/control")
    async def control() -> None:
        raise HTTPException(status, "declared", headers={"X-Control": "preserved"}) from cause

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/control")
    assert response.status_code == status
    assert response.json() == {"detail": "declared"}
    assert response.headers["x-control"] == "preserved"


async def test_http_error_handler_rejects_the_wrong_exception_class() -> None:
    with pytest.raises(TypeError, match="unexpected exception"):
        await framework_http_error(Request({"type": "http"}), ValueError("not HTTP"))
