from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx2 as httpx
import jwt
import pytest

from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubHeader,
    GitHubResponse,
    GitHubTransportFailure,
)
from ci_coordinator.integrations.github import app_transport as app_transport_module
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_ACCEPT,
    GITHUB_API_USER_AGENT,
    GITHUB_API_VERSION,
    GITHUB_APP_JWT_BACKDATE_SECONDS,
    GITHUB_APP_JWT_LIFETIME_SECONDS,
    GITHUB_MAXIMUM_REQUEST_BODY_BYTES,
)
from ci_coordinator.kernel import FixedClock

from ._github_app_transport_support import (
    APP_ID,
    INSTALLATION_TOKEN,
    NOW,
    _api_response,
    _api_response_handler,
    _factory,
    _oversize_handler,
    _private_key,
    _redirect_handler,
    _request,
    _run_credential_failure_case,
    _run_failure_case,
    _timeout_handler,
    _token_response,
)


def test_factory_issues_bound_app_jwt_and_authenticated_api_request() -> None:
    async def scenario() -> None:
        private_key = _private_key()
        public_key = private_key.public_key()
        observed: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            if request.url.path.endswith("/access_tokens"):
                token = request.headers["Authorization"].removeprefix("Bearer ")
                claims = jwt.decode(
                    token,
                    public_key,
                    algorithms=["RS256"],
                    options={"verify_exp": False},
                )
                assert claims == {
                    "iat": int(NOW.timestamp()) - GITHUB_APP_JWT_BACKDATE_SECONDS,
                    "exp": int(NOW.timestamp()) + GITHUB_APP_JWT_LIFETIME_SECONDS,
                    "iss": APP_ID,
                }
                return _token_response()
            assert request.headers["Authorization"] == f"Bearer {INSTALLATION_TOKEN}"
            assert request.headers["Accept"] == GITHUB_API_ACCEPT
            assert request.headers["User-Agent"] == GITHUB_API_USER_AGENT
            assert request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
            return httpx.Response(
                200,
                headers={
                    "X-GitHub-Api-Version-Selected": GITHUB_API_VERSION,
                    "X-RateLimit-Limit": "5000",
                    "X-RateLimit-Remaining": "4999",
                    "X-GitHub-Request-Id": "request-id",
                    "Set-Cookie": "provider-secret",
                },
                stream=httpx.ByteStream(b'{"provider":"github"}'),
            )

        factory = _factory(private_key, httpx.MockTransport(handler))
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()

        assert isinstance(result, GitHubResponse)
        assert result.api_version == GITHUB_API_VERSION
        assert result.rate_limit is not None
        assert result.rate_limit.remaining == 4999
        assert {header.name.casefold() for header in result.headers} == {
            "x-github-api-version-selected",
            "x-github-request-id",
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
        }
        assert [request.url.path for request in observed] == [
            "/app/installations/77/access_tokens",
            "/repositories/11",
        ]
        assert INSTALLATION_TOKEN not in repr(factory)

    asyncio.run(scenario())


def test_factory_rejects_identity_before_allocating_an_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_client(_transport: object) -> object:
        raise AssertionError("invalid identity must be rejected before HTTP client allocation")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(app_transport_module, "_GitHubAppHttpClient", unexpected_client)

    with pytest.raises(ValueError, match="App id"):
        GitHubAppTransportFactory(
            app_id=" invalid ",
            private_key_pem="unused-private-key",
            clock=FixedClock(NOW),
        )


def test_factory_refreshes_once_for_concurrent_bound_transports() -> None:
    async def scenario() -> None:
        token_requests = 0
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_requests
            if request.url.path.endswith("/access_tokens"):
                token_requests += 1
                refresh_started.set()
                await release_refresh.wait()
                return _token_response()
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            first = asyncio.create_task(factory.for_installation(77).send(_request()))
            await refresh_started.wait()
            second = asyncio.create_task(factory.for_installation(77).send(_request()))
            await asyncio.sleep(0)
            assert token_requests == 1
            release_refresh.set()
            results = await asyncio.gather(first, second)
        finally:
            await factory.aclose()

        assert all(isinstance(result, GitHubResponse) for result in results)
        assert token_requests == 1

    asyncio.run(scenario())


def test_factory_rejects_provider_response_that_reflects_the_installation_token() -> None:
    async def scenario() -> GitHubTransportFailure:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/access_tokens"):
                return _token_response()
            return httpx.Response(
                200,
                headers={
                    "x-github-api-version-selected": GITHUB_API_VERSION,
                    "x-github-request-id": INSTALLATION_TOKEN,
                },
                stream=httpx.ByteStream(json.dumps({"echo": INSTALLATION_TOKEN}).encode()),
            )

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result

    result = asyncio.run(scenario())

    assert result.kind == "unavailable"
    assert INSTALLATION_TOKEN not in repr(result)


def test_cancelling_one_shared_refresh_waiter_preserves_the_other_waiter() -> None:
    async def scenario() -> tuple[GitHubResponse | GitHubTransportFailure, int]:
        token_requests = 0
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_requests
            if request.url.path.endswith("/access_tokens"):
                token_requests += 1
                refresh_started.set()
                await release_refresh.wait()
                return _token_response()
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            cancelled = asyncio.create_task(factory.for_installation(77).send(_request()))
            await refresh_started.wait()
            preserved = asyncio.create_task(factory.for_installation(77).send(_request()))
            await asyncio.sleep(0)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            release_refresh.set()
            return await preserved, token_requests
        finally:
            await factory.aclose()

    result, token_requests = asyncio.run(scenario())

    assert isinstance(result, GitHubResponse)
    assert token_requests == 1


def test_factory_fails_closed_on_unadmitted_or_failed_provider_exchange() -> None:
    cases = (
        (
            _request(headers=(GitHubHeader("Authorization", "Bearer forged"),)),
            _api_response_handler,
            "unavailable",
            0,
        ),
        (
            _request(headers=(GitHubHeader("Host", "attacker.invalid"),)),
            _api_response_handler,
            "unavailable",
            0,
        ),
        (_request(path="//attacker.invalid/steal"), _api_response_handler, "unavailable", 0),
        (_request(api_version="2022-11-28"), _api_response_handler, "unavailable", 0),
        (
            _request(body=b"x" * (GITHUB_MAXIMUM_REQUEST_BODY_BYTES + 1)),
            _api_response_handler,
            "unavailable",
            0,
        ),
        (_request(), _redirect_handler, "unavailable", 2),
        (_request(), _oversize_handler, "unavailable", 2),
        (_request(), _timeout_handler, "timeout", 2),
    )
    for request, handler, expected_kind, expected_requests in cases:
        result, observed = asyncio.run(_run_failure_case(request, handler))

        assert result.kind == expected_kind
        assert observed == expected_requests
        assert INSTALLATION_TOKEN not in repr(result)


def test_factory_rejects_malformed_or_near_expiry_credential_without_api_request() -> None:
    async def scenario() -> tuple[GitHubTransportFailure, int]:
        observed = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal observed
            observed += 1
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
                            "expires_at": (NOW + timedelta(seconds=300)).isoformat(),
                        }
                    ).encode()
                ),
            )

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result, observed

    result, observed = asyncio.run(scenario())

    assert result.kind == "unavailable"
    assert observed == 1


def test_factory_rejects_malformed_or_version_mismatched_credential_response() -> None:
    cases: tuple[tuple[dict[str, str], str], ...] = (
        ({}, GITHUB_API_VERSION),
        (
            {
                "token": INSTALLATION_TOKEN,
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
            "2022-11-28",
        ),
    )
    for payload, selected_version in cases:
        result, observed = asyncio.run(_run_credential_failure_case(payload, selected_version))

        assert result.kind == "unavailable"
        assert observed == 1


def test_factory_rejects_duplicate_credential_provenance_header() -> None:
    async def scenario() -> tuple[GitHubTransportFailure, int]:
        observed = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal observed
            observed += 1
            return httpx.Response(
                201,
                headers=[
                    ("content-type", "application/json"),
                    ("x-github-api-version-selected", GITHUB_API_VERSION),
                    ("x-github-api-version-selected", GITHUB_API_VERSION),
                ],
                stream=httpx.ByteStream(
                    json.dumps(
                        {
                            "token": INSTALLATION_TOKEN,
                            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
                        }
                    ).encode()
                ),
            )

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result, observed

    result, observed = asyncio.run(scenario())

    assert result.kind == "unavailable"
    assert observed == 1


def test_factory_propagates_cancellation_during_credential_refresh() -> None:
    async def scenario() -> None:
        refresh_started = asyncio.Event()
        never_release = asyncio.Event()

        async def handler(_: httpx.Request) -> httpx.Response:
            refresh_started.set()
            await never_release.wait()
            raise AssertionError("cancelled request must not resume")

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            task = asyncio.create_task(factory.for_installation(77).send(_request()))
            await refresh_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await factory.aclose()

    asyncio.run(scenario())


def test_factory_total_deadline_includes_credential_refresh_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_timeout = asyncio.timeout
    refresh_started = False

    def immediate_timeout(delay: float | None) -> asyncio.Timeout:
        assert delay == 10
        return real_timeout(0)

    async def blocked_refresh(_: int) -> None:
        nonlocal refresh_started
        refresh_started = True
        await asyncio.Event().wait()

    async def scenario() -> GitHubTransportFailure:
        factory = _factory(_private_key(), httpx.MockTransport(_api_response_handler))
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(factory._credentials, "get", blocked_refresh)
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result

    monkeypatch.setattr(app_transport_module, "_request_deadline", immediate_timeout)
    result = asyncio.run(scenario())

    assert result.kind == "timeout"
    assert refresh_started is True


def test_cancelled_refresh_is_not_cached_and_a_retry_refreshes_again() -> None:
    async def scenario() -> None:
        token_requests = 0
        refresh_started = asyncio.Event()
        never_release = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_requests
            if request.url.path.endswith("/access_tokens"):
                token_requests += 1
                if token_requests == 1:
                    refresh_started.set()
                    await never_release.wait()
                return _token_response()
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        try:
            cancelled = asyncio.create_task(factory.for_installation(77).send(_request()))
            await refresh_started.wait()
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            await asyncio.sleep(0)
            retried = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()

        assert isinstance(retried, GitHubResponse)
        assert token_requests == 2

    asyncio.run(scenario())
