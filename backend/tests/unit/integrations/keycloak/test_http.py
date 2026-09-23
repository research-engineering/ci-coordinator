from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from urllib.parse import parse_qsl

import httpx2 as httpx
import pytest

from ci_coordinator.control_plane_identity import (
    KeycloakEvidenceRejected,
    KeycloakUnavailable,
)
from ci_coordinator.integrations.keycloak._http import (
    _admit_token_response,
    _HttpAdmissionFailure,
    _KeycloakHttpClient,
    _SharedLifecycle,
)

from ._support import REDIRECT_URI, response

Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def _client(handler: Handler) -> _KeycloakHttpClient:
    return _KeycloakHttpClient(
        client_id="browser-client",
        client_secret="client-secret",
        redirect_uri=REDIRECT_URI,
        transport=httpx.MockTransport(handler),
        outbound_proxy_url=None,
    )


def test_document_request_has_one_fixed_credential_free_shape() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return response(body=b'{"issuer":"exact"}')

    client = _client(handler)

    async def scenario() -> bytes:
        try:
            return await client.get_document("https://auth.example.test/document", maximum_bytes=32)
        finally:
            await client.aclose()

    assert asyncio.run(scenario()) == b'{"issuer":"exact"}'
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "GET"
    assert str(request.url) == "https://auth.example.test/document"
    assert request.headers["accept"] == "application/json"
    assert request.headers["accept-encoding"] == "identity"
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers


def _invalid_response(case: str) -> httpx.Response:
    if case == "status":
        return response(201)
    if case == "redirect":
        return response(302, headers={"content-type": "application/json", "location": "/next"})
    if case == "missing-content-type":
        return response(headers={})
    if case == "duplicate-content-type":
        return response(
            headers=[("content-type", "application/json"), ("content-type", "text/json")]
        )
    if case == "wrong-content-type":
        return response(headers={"content-type": "text/json"})
    if case == "content-encoding":
        return response(headers={"content-type": "application/json", "content-encoding": "gzip"})
    if case == "declared-body-bound":
        return response(headers={"content-type": "application/json", "content-length": "1025"})
    if case == "noncanonical-content-length":
        return response(headers={"content-type": "application/json", "content-length": "01"})
    if case == "duplicate-content-length":
        return response(
            headers=[
                ("content-type", "application/json"),
                ("content-length", "1"),
                ("content-length", "1"),
            ]
        )
    if case == "header-count":
        return response(
            headers=[("content-type", "application/json"), *((f"x-{i}", "v") for i in range(64))]
        )
    if case == "header-name":
        return response(headers={"content-type": "application/json", f"x-{'n' * 128}": "v"})
    if case == "header-value":
        return response(headers={"content-type": "application/json", "x-value": "v" * 8_193})
    if case == "body-bound":
        return response(body=b"012345678")
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "status",
        "redirect",
        "missing-content-type",
        "duplicate-content-type",
        "wrong-content-type",
        "content-encoding",
        "declared-body-bound",
        "noncanonical-content-length",
        "duplicate-content-length",
        "header-count",
        "header-name",
        "header-value",
        "body-bound",
    ],
)
def test_document_response_admission_rejects_each_bounded_countermodel(case: str) -> None:
    client = _client(lambda _: _invalid_response(case))

    async def scenario() -> None:
        try:
            with pytest.raises(KeycloakUnavailable):
                await client.get_document(
                    "https://auth.example.test/document",
                    maximum_bytes=8 if case == "body-bound" else 1_024,
                )
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "body", "content_type"),
    [
        (201, b'{"id_token":"a.b.c"}', "application/json"),
        (200, b'{"access_token":"a.b.c"}', "application/json"),
        (200, b'{"id_token":"a.b.c","error":"invalid"}', "application/json"),
        (400, b'{"error":1}', "application/json"),
        (400, b'{"error":""}', "application/json"),
        (400, b'{"error":"invalid_grant"}', "text/json"),
        (200, b"[]", "application/json"),
        (200, b'{"id_token":""}', "application/json"),
        (200, b'{"id_token":"a.b.c","nested":{}}', "application/json"),
    ],
)
def test_token_response_admission_rejects_status_schema_and_media_type_countermodels(
    status: int,
    body: bytes,
    content_type: str,
) -> None:
    with pytest.raises((_HttpAdmissionFailure, TypeError, ValueError)):
        _admit_token_response(response(status, body=body, headers={"content-type": content_type}))


def test_document_admission_rejects_immediately_instead_of_queueing() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response()

    client = _client(handler)
    leases = [client._request_admission.try_acquire() for _ in range(16)]
    assert all(lease is not None for lease in leases)

    async def scenario() -> None:
        try:
            with pytest.raises(KeycloakUnavailable):
                await client.get_document("https://auth.example.test/document", maximum_bytes=8)
        finally:
            for lease in leases:
                assert lease is not None
                lease.release()
            await client.aclose()

    asyncio.run(scenario())
    assert calls == 0


def test_code_exchange_uses_basic_auth_pkce_and_erases_authlib_token_state() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return response(
            body=(
                b'{"access_token":"access.canary.value","expires_in":60,'
                b'"id_token":"id.canary.value","token_type":"Bearer"}'
            )
        )

    client = _client(handler)

    async def scenario() -> str:
        try:
            result = await client.exchange_code(
                token_endpoint="https://auth.example.test/token",
                code="authorization-code",
                code_verifier="A" * 43,
            )
            assert client._client.token is None
            return result
        finally:
            await client.aclose()

    assert asyncio.run(scenario()) == "id.canary.value"
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "POST"
    assert str(request.url) == "https://auth.example.test/token"
    assert request.headers["authorization"].startswith("Basic ")
    assert dict(parse_qsl(request.content.decode(), strict_parsing=True)) == {
        "code": "authorization-code",
        "code_verifier": "A" * 43,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("invalid_grant", KeycloakEvidenceRejected),
        ("temporarily_unavailable", KeycloakUnavailable),
    ],
)
def test_code_exchange_preserves_rejected_vs_unavailable_failure_algebra(
    error: str,
    expected: type[Exception],
) -> None:
    client = _client(lambda _: response(400, body=f'{{"error":"{error}"}}'.encode()))

    async def scenario() -> None:
        try:
            with pytest.raises(expected):
                await client.exchange_code(
                    token_endpoint="https://auth.example.test/token",
                    code="authorization-code",
                    code_verifier="A" * 43,
                )
            assert client._client.token is None
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_code_exchange_admission_rejects_immediately_instead_of_queueing() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response()

    client = _client(handler)
    lease = client._oauth_admission.try_acquire()
    assert lease is not None

    async def scenario() -> None:
        try:
            with pytest.raises(KeycloakUnavailable):
                await client.exchange_code(
                    token_endpoint="https://auth.example.test/token",
                    code="authorization-code",
                    code_verifier="A" * 43,
                )
        finally:
            lease.release()
            await client.aclose()

    asyncio.run(scenario())
    assert calls == 0


def test_cancellation_releases_request_capacity_without_translation() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(_: httpx.Request) -> httpx.Response:
            started.set()
            await release.wait()
            return response(body=b"{}")

        client = _client(handler)
        request = asyncio.create_task(
            client.get_document("https://auth.example.test/document", maximum_bytes=8)
        )
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

        release.set()
        assert (
            await client.get_document("https://auth.example.test/document", maximum_bytes=8)
            == b"{}"
        )
        await client.aclose()

    asyncio.run(scenario())


def test_streaming_body_limit_rejects_before_unbounded_materialization() -> None:
    class TwoChunkStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"1234"
            yield b"56789"

        async def aclose(self) -> None:
            self.closed = True

    stream = TwoChunkStream()
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=stream,
        )
    )

    async def scenario() -> None:
        with pytest.raises(KeycloakUnavailable):
            await client.get_document("https://auth.example.test/document", maximum_bytes=8)
        await client.aclose()

    asyncio.run(scenario())
    assert stream.closed is True


def test_declared_body_limit_rejects_before_stream_iteration() -> None:
    class UnreadableStream(httpx.AsyncByteStream):
        iterated = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.iterated = True
            raise AssertionError("rejected Keycloak response body must not be read")
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            return None

    stream = UnreadableStream()
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": "9"},
            stream=stream,
        )
    )

    async def scenario() -> None:
        try:
            with pytest.raises(KeycloakUnavailable):
                await client.get_document(
                    "https://auth.example.test/document",
                    maximum_bytes=8,
                )
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert stream.iterated is False


def test_shared_lifecycle_drains_retries_close_and_shares_cancelled_callers() -> None:
    async def scenario() -> None:
        lifecycle = _SharedLifecycle()
        assert await lifecycle.enter() is True
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        attempts = 0

        async def close_resource() -> None:
            nonlocal attempts
            attempts += 1
            close_started.set()
            await release_close.wait()
            if attempts == 1:
                raise OSError("transient")

        draining = asyncio.create_task(lifecycle.close(close_resource))
        assert close_started.is_set() is False
        await lifecycle.leave()
        await close_started.wait()
        release_close.set()
        with pytest.raises(OSError, match="transient"):
            await draining
        assert lifecycle.is_open is True

        release_close = asyncio.Event()
        second_started = asyncio.Event()

        async def successful_close() -> None:
            nonlocal attempts
            attempts += 1
            second_started.set()
            await release_close.wait()

        caller = asyncio.create_task(lifecycle.close(successful_close))
        await second_started.wait()
        peer = asyncio.create_task(lifecycle.close(successful_close))
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release_close.set()
        await peer
        await lifecycle.close(successful_close)
        assert attempts == 2
        assert lifecycle.is_open is False
        assert await lifecycle.enter() is False

    asyncio.run(scenario())
