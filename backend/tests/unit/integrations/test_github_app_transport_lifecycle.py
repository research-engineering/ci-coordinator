from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx2 as httpx
import pytest
from cryptography.hazmat.primitives import serialization

from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubResponse,
    GitHubTransportFailure,
)
from ci_coordinator.integrations.github.app_lifecycle import _SharedClientLifecycle
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_VERSION,
)
from ci_coordinator.kernel import FixedClock

from ._github_app_transport_support import (
    APP_ID,
    INSTALLATION_TOKEN,
    NOW,
    _api_response,
    _api_response_handler,
    _factory,
    _private_key,
    _request,
    _token_response,
)


def test_factory_drains_an_accepted_send_before_closing_the_shared_client() -> None:
    async def scenario() -> tuple[GitHubResponse | GitHubTransportFailure, GitHubTransportFailure]:
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/access_tokens"):
                refresh_started.set()
                await release_refresh.wait()
                return _token_response()
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        bound_transport = factory.for_installation(77)
        send = asyncio.create_task(bound_transport.send(_request()))
        await refresh_started.wait()
        close = asyncio.create_task(factory.aclose())
        await asyncio.sleep(0)
        assert not close.done()
        release_refresh.set()
        result = await send
        await close
        after_close = await bound_transport.send(_request())
        assert isinstance(after_close, GitHubTransportFailure)
        return result, after_close

    result, after_close = asyncio.run(scenario())

    assert isinstance(result, GitHubResponse)
    assert isinstance(after_close, GitHubTransportFailure)
    assert after_close.kind == "unavailable"


def test_shared_client_close_failure_is_retryable_without_reopening_admission() -> None:
    async def scenario() -> None:
        lifecycle = _SharedClientLifecycle()
        attempts = 0

        async def close_resource() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("transient close failure")

        with pytest.raises(OSError, match="transient close failure"):
            await lifecycle.close(close_resource)

        assert lifecycle.is_open is False
        assert await lifecycle.enter_send() is False

        await lifecycle.close(close_resource)
        await lifecycle.close(close_resource)

        assert attempts == 2

    asyncio.run(scenario())


def test_concurrent_shared_client_close_callers_share_each_attempt() -> None:
    async def scenario() -> None:
        lifecycle = _SharedClientLifecycle()
        attempts = 0
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        retry_started = asyncio.Event()
        release_retry = asyncio.Event()
        transient_failure = OSError("transient close failure")

        async def close_resource() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                first_started.set()
                await release_first.wait()
                raise transient_failure
            if attempts == 2:
                retry_started.set()
                await release_retry.wait()
                return
            raise AssertionError("successful close must be terminal")

        first = asyncio.create_task(lifecycle.close(close_resource))
        await first_started.wait()
        first_peer = asyncio.create_task(lifecycle.close(close_resource))
        await asyncio.sleep(0)
        release_first.set()
        first_results = await asyncio.gather(first, first_peer, return_exceptions=True)

        assert first_results[0] is transient_failure
        assert first_results[1] is transient_failure
        assert attempts == 1

        retry = asyncio.create_task(lifecycle.close(close_resource))
        await retry_started.wait()
        retry_peer = asyncio.create_task(lifecycle.close(close_resource))
        await asyncio.sleep(0)

        assert retry.done() is False
        assert retry_peer.done() is False
        assert attempts == 2

        release_retry.set()
        await asyncio.gather(retry, retry_peer)
        await lifecycle.close(close_resource)

        assert attempts == 2

    asyncio.run(scenario())


def test_factory_rejects_near_expiry_token_using_time_after_provider_exchange() -> None:
    class AdvancingClock:
        def __init__(self) -> None:
            self.value = NOW

        def now(self) -> datetime:
            return self.value

    async def scenario() -> GitHubTransportFailure:
        clock = AdvancingClock()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/access_tokens"):
                clock.value = NOW + timedelta(seconds=2)
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
                                "expires_at": (NOW + timedelta(seconds=301)).isoformat(),
                            }
                        ).encode()
                    ),
                )
            raise AssertionError("near-expiry token must not reach the ordinary API")

        factory = GitHubAppTransportFactory(
            app_id=APP_ID,
            private_key_pem=_private_key()
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode(),
            clock=clock,
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await factory.for_installation(77).send(_request())
        finally:
            await factory.aclose()
        assert isinstance(result, GitHubTransportFailure)
        return result

    assert asyncio.run(scenario()).kind == "unavailable"


def test_factory_bounds_failed_refresh_registry_and_does_not_render_secrets() -> None:
    async def scenario() -> GitHubAppTransportFactory:
        private_key = "PRIVATE-KEY-CANARY"
        factory = GitHubAppTransportFactory(
            app_id=APP_ID,
            private_key_pem=private_key,
            clock=FixedClock(NOW),
            transport=httpx.MockTransport(_api_response_handler),
        )
        try:
            for installation_id in range(1, 129):
                result = await factory.for_installation(installation_id).send(_request())
                assert isinstance(result, GitHubTransportFailure)
                assert private_key not in repr(result)
                assert private_key not in str(result)
            await asyncio.sleep(0)
            assert factory._credentials._refresh_tasks == {}
            assert factory._credentials._cache == {}
        finally:
            await factory.aclose()
        return factory

    factory = asyncio.run(scenario())
    assert "PRIVATE-KEY-CANARY" not in repr(factory)
