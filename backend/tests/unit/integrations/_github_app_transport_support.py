from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubHeader,
    GitHubRequest,
    GitHubTransportFailure,
)
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_VERSION,
    GITHUB_MAXIMUM_RESPONSE_BODY_BYTES,
)
from ci_coordinator.kernel import FixedClock

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
APP_ID = "12345"
INSTALLATION_TOKEN = "installation-token-must-not-leak"
type ResponseHandler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def _factory(
    private_key: rsa.RSAPrivateKey,
    transport: httpx.AsyncBaseTransport,
) -> GitHubAppTransportFactory:
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return GitHubAppTransportFactory(
        app_id=APP_ID,
        private_key_pem=private_key_pem,
        clock=FixedClock(NOW),
        transport=transport,
    )


def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _request(
    *,
    api_version: str = GITHUB_API_VERSION,
    headers: tuple[GitHubHeader, ...] = (),
    body: bytes | None = None,
    path: str = "/repositories/11",
) -> GitHubRequest:
    return GitHubRequest(
        operation="repositories.get_by_id",
        method="GET",
        path=path,
        api_version=api_version,
        headers=headers,
        body=body,
    )


def _token_response() -> httpx.Response:
    return httpx.Response(
        201,
        headers={
            "content-type": "application/json",
            "x-github-api-version-selected": GITHUB_API_VERSION,
        },
        stream=httpx.ByteStream(
            json.dumps(
                {
                    "token": INSTALLATION_TOKEN,
                    "expires_at": (NOW + timedelta(hours=1)).isoformat(),
                }
            ).encode()
        ),
    )


def _api_response() -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-github-api-version-selected": GITHUB_API_VERSION},
        stream=httpx.ByteStream(b"{}"),
    )


async def _api_response_handler(_: httpx.Request) -> httpx.Response:
    return _api_response()


async def _redirect_handler(_: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"location": "https://attacker.invalid"})


async def _oversize_handler(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-github-api-version-selected": GITHUB_API_VERSION},
        stream=httpx.ByteStream(b"x" * (GITHUB_MAXIMUM_RESPONSE_BODY_BYTES + 1)),
    )


async def _timeout_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("deadline", request=request)


async def _run_failure_case(
    request: GitHubRequest,
    handler: ResponseHandler,
) -> tuple[GitHubTransportFailure, int]:
    observed = 0

    async def counting_handler(incoming: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed += 1
        if incoming.url.path.endswith("/access_tokens"):
            return _token_response()
        return await handler(incoming)

    factory = _factory(_private_key(), httpx.MockTransport(counting_handler))
    try:
        result = await factory.for_installation(77).send(request)
    finally:
        await factory.aclose()
    assert isinstance(result, GitHubTransportFailure)
    return result, observed


async def _run_credential_failure_case(
    payload: dict[str, str],
    selected_version: str,
) -> tuple[GitHubTransportFailure, int]:
    observed = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed += 1
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-github-api-version-selected": selected_version,
            },
            stream=httpx.ByteStream(json.dumps(payload).encode()),
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    try:
        result = await factory.for_installation(77).send(_request())
    finally:
        await factory.aclose()
    assert isinstance(result, GitHubTransportFailure)
    return result, observed
