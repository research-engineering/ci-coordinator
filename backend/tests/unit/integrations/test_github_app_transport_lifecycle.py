from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx2 as httpx
import pytest
from cryptography.hazmat.primitives import serialization

from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubQueryParameter,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportFailure,
)
from ci_coordinator.integrations.github import app_credentials as credential_module
from ci_coordinator.integrations.github.app_lifecycle import _SharedClientLifecycle
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_VERSION,
    GITHUB_MAXIMUM_CACHED_INSTALLATIONS,
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
        bound_transport = factory.for_installation(77, repository_id=11)
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


def test_shared_client_close_retains_failure_without_reopening_admission() -> None:
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

        for _ in range(2):
            with pytest.raises(OSError, match="transient close failure"):
                await lifecycle.close(close_resource)

        assert attempts == 1

    asyncio.run(scenario())


def test_concurrent_shared_client_close_callers_share_the_retained_failure() -> None:
    async def scenario() -> None:
        lifecycle = _SharedClientLifecycle()
        attempts = 0
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        transient_failure = OSError("transient close failure")

        async def close_resource() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                first_started.set()
                await release_first.wait()
                raise transient_failure
            raise AssertionError("unsuccessful HTTP close must not be repeated")

        first = asyncio.create_task(lifecycle.close(close_resource))
        await first_started.wait()
        first_peer = asyncio.create_task(lifecycle.close(close_resource))
        await asyncio.sleep(0)
        release_first.set()
        first_results = await asyncio.gather(first, first_peer, return_exceptions=True)

        assert first_results[0] is transient_failure
        assert first_results[1] is transient_failure
        assert attempts == 1

        repeated = await asyncio.gather(
            lifecycle.close(close_resource), lifecycle.close(close_resource), return_exceptions=True
        )
        assert all(result is transient_failure for result in repeated)
        assert attempts == 1

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
            result = await factory.for_installation(77, repository_id=11).send(_request())
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
                result = await factory.for_installation(installation_id, repository_id=11).send(
                    _request()
                )
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


def test_cache_ceiling_counts_all_grants_not_installations(monkeypatch: pytest.MonkeyPatch) -> None:
    assert GITHUB_MAXIMUM_CACHED_INSTALLATIONS == 1_024

    def app_jwt(_app_id: str, _private_key: str, _now: datetime) -> str:
        return "synthetic-app-token"

    monkeypatch.setattr(credential_module, "_issue_app_jwt", app_jwt)

    async def scenario() -> None:
        mints = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mints
            if request.url.path.endswith("/access_tokens"):
                mints += 1
                return _token_response(token=f"scoped-{mints}")
            return _api_response()

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        page = (GitHubQueryParameter("page", "1"), GitHubQueryParameter("per_page", "100"))
        try:
            for repository_id in range(1, 1_024):
                result = await factory.for_installation(77, repository_id=repository_id).send(
                    _request(path=f"/repositories/{repository_id}")
                )
                assert isinstance(result, GitHubResponse)
            assert mints == 1_023 and len(factory._credentials._cache) == 1_023
            extra = (
                (
                    77,
                    None,
                    GitHubRequest(
                        "provider_inventory.list_repositories",
                        "GET",
                        "/installation/repositories",
                        GITHUB_API_VERSION,
                        query=page,
                    ),
                ),
                (
                    77,
                    11,
                    GitHubRequest(
                        "runner.list_group_self_hosted_runners",
                        "GET",
                        "/orgs/example/actions/runner-groups/7/runners",
                        GITHUB_API_VERSION,
                        query=page,
                    ),
                ),
                (
                    77,
                    11,
                    GitHubRequest(
                        "actions.get_workflow_run",
                        "GET",
                        "/repos/example/ci/actions/runs/7",
                        GITHUB_API_VERSION,
                    ),
                ),
                (88, 11, _request()),
            )
            for installation_id, bound_repository_id, request in extra:
                result = await factory.for_installation(
                    installation_id, repository_id=bound_repository_id
                ).send(request)
                assert isinstance(result, GitHubResponse)
                assert len(factory._credentials._cache) == 1_024
            assert mints == 1_027
            assert isinstance(
                await factory.for_installation(77, repository_id=1_023).send(
                    _request(path="/repositories/1023")
                ),
                GitHubResponse,
            )
            assert mints == 1_027
            assert isinstance(
                await factory.for_installation(77, repository_id=1).send(
                    _request(path="/repositories/1")
                ),
                GitHubResponse,
            )
            assert mints == 1_028 and len(factory._credentials._cache) == 1_024
            assert not factory._credentials._refresh_tasks
            assert not factory._credentials._refresh_waiters
        finally:
            await factory.aclose()

    asyncio.run(scenario())
