from __future__ import annotations

import asyncio
import json
import traceback
from typing import Literal, cast

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from integrations._github_app_transport_support import (
    _api_response,
    _factory,
    _private_key,
    _request,
    _token_response,
)
from integrations.keycloak import _support as identity_fixture
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.control_plane_identity import KeycloakUnavailable
from ci_coordinator.identity_admission import JwksTransportFailure, JwksUnavailable
from ci_coordinator.integrations import GITHUB_ACTIONS_JWKS_URI, GitHubActionsJwksProvider
from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubResponse,
    GitHubTransportFailure,
)
from ci_coordinator.integrations.keycloak.client import KeycloakIntegration
from ci_coordinator.observability import BackgroundHealth, BackgroundHealthState
from ci_coordinator.reconciliation import ReconciliationScheduler
from ci_coordinator.runtime.reconciliation_service import (
    InitialReconciliationError,
    PeriodicReconciliationService,
)
from ci_coordinator.runtime.resources import (
    RuntimeAsyncResource,
    RuntimeBackgroundService,
    RuntimeResources,
)

_HttpOwner = GitHubAppTransportFactory | KeycloakIntegration | GitHubActionsJwksProvider
_HttpKind = Literal["github", "keycloak", "jwks"]
_CloseOutcome = Literal["success", "error", "cancel"]


class _PartialHttpTransport(httpx.AsyncBaseTransport):
    def __init__(self, outcome: _CloseOutcome, *, blocked: bool = False) -> None:
        self.outcome = outcome
        self.owned = {"A", "B"}
        self.requests: list[str] = []
        self.close_calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.settled = asyncio.Event()
        self.failure = OSError("transport-close-secret-canary")
        if not blocked:
            self.release.set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if request.url.path.endswith("/access_tokens"):
            assert json.loads(request.content) == {
                "permissions": {"metadata": "read"},
                "repository_ids": [11],
            }
            return _token_response()
        if str(request.url) == identity_fixture.DISCOVERY_URL:
            return identity_fixture.response(body=identity_fixture.discovery_document())
        if str(request.url) in {identity_fixture.JWKS_URI, GITHUB_ACTIONS_JWKS_URI}:
            return identity_fixture.response(
                body=identity_fixture.json_bytes({"keys": [identity_fixture.public_jwk()]})
            )
        assert request.url.path == "/repositories/11"
        return _api_response()

    async def aclose(self) -> None:
        self.close_calls += 1
        self.owned.discard("A")
        self.entered.set()
        try:
            await self.release.wait()
            if self.close_calls == 1:
                if self.outcome == "error":
                    raise self.failure
                if self.outcome == "cancel":
                    raise asyncio.CancelledError
            self.owned.discard("B")
        finally:
            self.settled.set()


async def _prepared_http_owner(kind: _HttpKind, transport: _PartialHttpTransport) -> _HttpOwner:
    if kind == "github":
        factory = _factory(_private_key(), transport)
        assert isinstance(
            await factory.for_installation(77, repository_id=11).send(_request()), GitHubResponse
        )
        return factory
    if kind == "jwks":
        provider = GitHubActionsJwksProvider(
            identity_fixture.ManualMonotonicClock(), transport=transport
        )
        assert not isinstance(await provider.get_key_set(identity_fixture.KID), JwksUnavailable)
        return provider
    integration = KeycloakIntegration(
        issuer=identity_fixture.ISSUER,
        browser_client_id=identity_fixture.BROWSER_CLIENT_ID,
        browser_client_secret="browser-secret",
        api_client_id=identity_fixture.API_CLIENT_ID,
        redirect_uri=identity_fixture.REDIRECT_URI,
        post_logout_redirect_uri=identity_fixture.POST_LOGOUT_REDIRECT_URI,
        clock=identity_fixture.ManualMonotonicClock(),
        transport=transport,
    )
    await integration.prepare()
    assert await integration.machine.verify(identity_fixture.token("access"))
    return integration


def _http_close_task(owner: _HttpOwner) -> asyncio.Task[None] | None:
    if isinstance(owner, GitHubAppTransportFactory):
        return owner._lifecycle._close_task
    if isinstance(owner, KeycloakIntegration):
        return owner._http._lifecycle._close_task
    return owner._transport._close_task


async def _assert_http_admission_closed(owner: _HttpOwner) -> None:
    if isinstance(owner, GitHubAppTransportFactory):
        with pytest.raises(RuntimeError, match="factory is closed"):
            owner.for_installation(77, repository_id=11)
        with pytest.raises(RuntimeError, match="factory is closed"):
            owner.for_app()
        with pytest.raises(RuntimeError, match="factory is closed"):
            owner.for_reviewer("reviewer")
    elif isinstance(owner, KeycloakIntegration):
        with pytest.raises(KeycloakUnavailable):
            await owner.prepare()
        with pytest.raises(KeycloakUnavailable):
            owner.browser.authorization_url(
                state=identity_fixture.OPAQUE,
                nonce=identity_fixture.OPAQUE,
                code_challenge=identity_fixture.OPAQUE,
            )
        with pytest.raises(KeycloakUnavailable):
            await owner.browser.exchange_code(
                code="valid-shaped-code",
                code_verifier=identity_fixture.OPAQUE,
                expected_nonce=identity_fixture.OPAQUE,
            )
        with pytest.raises(KeycloakUnavailable):
            await owner.machine.verify(identity_fixture.token("access"))
        assert owner._http.is_open is False
    else:
        assert isinstance(await owner.get_key_set(identity_fixture.KID), JwksUnavailable)
        assert await owner.probe() is False
        assert isinstance(await owner._transport.fetch(), JwksTransportFailure)


def _resources_with_http_owner(
    owner: _HttpOwner, events: list[str], *, timeout: float = 5
) -> RuntimeResources:
    return RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        owner
        if isinstance(owner, GitHubAppTransportFactory)
        else cast(GitHubAppTransportFactory, _Closeable(events, "github")),
        owner
        if isinstance(owner, GitHubActionsJwksProvider)
        else cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        additional_resources=(owner,) if isinstance(owner, KeycloakIntegration) else (),
        shutdown_timeout_seconds=timeout,
    )


@pytest.mark.parametrize("kind", ["github", "keycloak", "jwks"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_actual_http_owner_retains_first_close_outcome(
    kind: _HttpKind, outcome: _CloseOutcome
) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(5):
            loop = asyncio.get_running_loop()
            contexts: list[dict[str, object]] = []
            loop.set_exception_handler(lambda _loop, context: contexts.append(context))
            transport = _PartialHttpTransport(outcome)
            owner = await _prepared_http_owner(kind, transport)
            bound = (
                owner.for_installation(77, repository_id=11)
                if isinstance(owner, GitHubAppTransportFactory)
                else None
            )
            requests = list(transport.requests)
            for _ in range(3):
                if outcome == "success":
                    await owner.aclose()
                elif outcome == "error":
                    with pytest.raises(OSError) as error:
                        await owner.aclose()
                    assert error.value is transport.failure
                else:
                    with pytest.raises(asyncio.CancelledError):
                        await owner.aclose()
                assert transport.owned == (set() if outcome == "success" else {"B"})
                assert transport.close_calls == 1
                await _assert_http_admission_closed(owner)
                if bound is not None:
                    assert isinstance(await bound.send(_request()), GitHubTransportFailure)
                assert transport.requests == requests
            callbacks_finished = asyncio.Event()
            loop.call_soon(callbacks_finished.set)
            await callbacks_finished.wait()
            assert contexts == []

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["github", "keycloak", "jwks"])
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_actual_http_close_is_shared_and_one_waiter_cannot_cancel_it(
    kind: _HttpKind, outcome: _CloseOutcome
) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(5):
            transport = _PartialHttpTransport(outcome, blocked=True)
            owner = await _prepared_http_owner(kind, transport)
            first = asyncio.create_task(owner.aclose())
            await transport.entered.wait()
            peer_entered = asyncio.Event()

            async def peer_close() -> None:
                peer_entered.set()
                await owner.aclose()

            peer = asyncio.create_task(peer_close())
            await peer_entered.wait()
            retained = _http_close_task(owner)
            assert retained is not None and not retained.done()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not retained.cancelled()
            assert not peer.done()
            assert transport.owned == {"B"}
            transport.release.set()
            if outcome == "success":
                await peer
                await owner.aclose()
            else:
                with pytest.raises(OSError) as error:
                    await peer
                assert error.value is transport.failure
                with pytest.raises(OSError):
                    await owner.aclose()
            assert _http_close_task(owner) is retained
            assert transport.close_calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["github", "keycloak", "jwks"])
def test_actual_http_close_observes_orphan_failure_without_rendering(kind: _HttpKind) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(5):
            loop = asyncio.get_running_loop()
            previous = loop.get_exception_handler()
            contexts: list[dict[str, object]] = []
            observed_tasks: tuple[asyncio.Task[None], ...] = ()
            loop.set_exception_handler(lambda _loop, context: contexts.append(context))
            try:
                transport = _PartialHttpTransport("error", blocked=True)
                owner = await _prepared_http_owner(kind, transport)
                caller = asyncio.create_task(owner.aclose())
                await transport.entered.wait()
                peer_entered = asyncio.Event()

                async def peer_close() -> None:
                    peer_entered.set()
                    await owner.aclose()

                peer = asyncio.create_task(peer_close())
                await peer_entered.wait()
                caller.cancel()
                peer.cancel()
                cancelled = await asyncio.gather(caller, peer, return_exceptions=True)
                assert all(isinstance(result, asyncio.CancelledError) for result in cancelled)
                retained = _http_close_task(owner)
                assert retained is not None
                outer = owner._close_task if isinstance(owner, KeycloakIntegration) else retained
                assert outer is not None
                if isinstance(owner, KeycloakIntegration):
                    assert outer is not retained
                observed_tasks = (retained, outer)
                outer_finished = asyncio.Event()
                outer.add_done_callback(lambda _task: outer_finished.set())
                transport.release.set()
                await outer_finished.wait()
                callbacks_finished = asyncio.Event()
                loop.call_soon(callbacks_finished.set)
                await callbacks_finished.wait()
                assert retained.done() and not retained.cancelled()
                # CPython's unread-exception marker is checked before any test-side result read.
                assert retained._log_traceback is False
                assert outer._log_traceback is False
                assert contexts == []
                with pytest.raises(OSError) as error:
                    await owner.aclose()
                assert error.value is transport.failure
                assert transport.close_calls == 1 and transport.owned == {"B"}
            finally:
                for task in observed_tasks:
                    if task.done() and not task.cancelled():
                        task.exception()
                loop.set_exception_handler(previous)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["github", "keycloak", "jwks"])
def test_runtime_never_marks_partial_http_cleanup_complete_on_retry(kind: _HttpKind) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(5):
            transport = _PartialHttpTransport("error")
            owner = await _prepared_http_owner(kind, transport)
            events: list[str] = []
            resources = _resources_with_http_owner(owner, events)
            for _ in range(3):
                with pytest.raises(RuntimeError, match="runtime resource cleanup failed") as error:
                    await resources.aclose()
                assert error.value.__cause__ is None
                assert error.value.__context__ is None
                assert "transport-close-secret-canary" not in "".join(
                    traceback.format_exception(error.value)
                )
                assert resources.is_open and not resources.lifecycle_ready
                assert not resources.cleanup_pending
                assert resources._github_closed is (kind != "github")
                assert resources._jwks_closed is (kind != "jwks")
                assert resources._additional_closed == set()
                assert transport.close_calls == 1 and transport.owned == {"B"}
            assert (
                events
                == {
                    "github": ["jwks", "engine"],
                    "keycloak": ["github", "jwks", "engine"],
                    "jwks": ["github", "engine"],
                }[kind]
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["github", "keycloak", "jwks"])
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_runtime_deadline_cannot_renew_or_hide_retained_http_close(
    kind: _HttpKind, outcome: _CloseOutcome
) -> None:
    async def scenario() -> None:
        async with asyncio.timeout(5):
            transport = _PartialHttpTransport(outcome, blocked=True)
            owner = await _prepared_http_owner(kind, transport)
            events: list[str] = []
            resources = _resources_with_http_owner(owner, events, timeout=0.1)
            first = asyncio.create_task(resources.aclose())
            await transport.entered.wait()
            retained = _http_close_task(owner)
            assert retained is not None
            with pytest.raises(RuntimeError, match="cleanup exceeded its deadline"):
                await first
            for _ in range(2):
                with pytest.raises(RuntimeError, match="cleanup exceeded its deadline"):
                    await resources.aclose()
            assert not retained.done()
            assert transport.close_calls == 1 and transport.owned == {"B"}
            transport.release.set()
            if outcome == "success":
                await owner.aclose()
            else:
                with pytest.raises(OSError):
                    await owner.aclose()
            with pytest.raises(RuntimeError, match="cleanup exceeded its deadline"):
                await resources.aclose()
            assert resources.is_open and not resources.lifecycle_ready
            assert resources._additional_closed == set()
            assert resources._jwks_closed is False
            assert resources._github_closed is (kind == "jwks")
            assert events == (["github"] if kind == "jwks" else [])
            assert transport.close_calls == 1

    asyncio.run(scenario())


class _Closeable:
    def __init__(
        self,
        events: list[str],
        name: str,
        *,
        failures: int = 0,
    ) -> None:
        self._events = events
        self._name = name
        self._failures = failures

    async def prepare(self) -> None:
        self._events.append(f"{self._name}-prepare")

    async def aclose(self) -> None:
        self._events.append(self._name)
        if self._failures:
            self._failures -= 1
            raise RuntimeError(f"{self._name} close failed")


class _Engine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def dispose(self) -> None:
        self._events.append("engine")


class _Background:
    def __init__(
        self,
        events: list[str],
        *,
        safe_to_close: bool = True,
        start_failure: BaseException | None = None,
        health_state: BackgroundHealthState = BackgroundHealthState.HEALTHY,
    ) -> None:
        self._events = events
        self._safe_to_close = safe_to_close
        self._start_failure = start_failure
        self.health_state = health_state

    async def start(self) -> None:
        self._events.append("background-start")
        if self._start_failure is not None:
            raise self._start_failure

    async def stop(self) -> bool:
        self._events.append("background-stop")
        return self._safe_to_close

    def permit_close(self) -> None:
        self._safe_to_close = True

    def background_health(self) -> BackgroundHealth:
        return BackgroundHealth(self.health_state)


def test_lifespan_drains_background_before_external_resources() -> None:
    events: list[str] = []
    resources = _resources(
        events,
        _Background(events),
        additional_resources=(_Closeable(events, "keycloak"),),
    )

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            events.append("serving")

    asyncio.run(scenario())

    assert events == [
        "keycloak-prepare",
        "background-start",
        "serving",
        "background-stop",
        "keycloak",
        "github",
        "jwks",
        "engine",
    ]


def test_resource_prepare_failure_prevents_serving_and_closes_every_resource() -> None:
    events: list[str] = []

    class FailingPrepare(_Closeable):
        async def prepare(self) -> None:
            events.append("keycloak-prepare")
            raise RuntimeError("identity provider unavailable")

    resources = _resources(
        events,
        _Background(events),
        additional_resources=(FailingPrepare(events, "keycloak"),),
    )

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            events.append("serving")

    with pytest.raises(RuntimeError, match="identity provider unavailable"):
        asyncio.run(scenario())

    assert events == ["keycloak-prepare", "keycloak", "github", "jwks", "engine"]


def test_resources_are_ready_only_during_an_active_lifespan() -> None:
    events: list[str] = []
    resources = _resources(events, _Background(events))

    async def scenario() -> tuple[bool, bool, bool]:
        before = resources.lifecycle_ready
        async with resources.lifespan(FastAPI()):
            active = resources.lifecycle_ready
        return before, active, resources.lifecycle_ready

    assert asyncio.run(scenario()) == (False, True, False)


def test_terminal_background_failure_is_projected_without_exception_details() -> None:
    events: list[str] = []
    background = _Background(
        events,
        health_state=BackgroundHealthState.TERMINAL_FAILURE,
    )
    resources = _resources(events, background)

    async def scenario() -> BackgroundHealth:
        async with resources.lifespan(FastAPI()):
            return resources.background_health()

    assert asyncio.run(scenario()) == BackgroundHealth(BackgroundHealthState.TERMINAL_FAILURE)


def test_unobservable_background_health_fails_closed() -> None:
    events: list[str] = []

    class UnobservableBackground:
        async def start(self) -> None:
            events.append("background-start")

        async def stop(self) -> bool:
            events.append("background-stop")
            return True

    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, _Closeable(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        cast(RuntimeBackgroundService, UnobservableBackground()),
    )

    async def scenario() -> BackgroundHealth:
        async with resources.lifespan(FastAPI()):
            return resources.background_health()

    assert asyncio.run(scenario()) == BackgroundHealth(BackgroundHealthState.UNOBSERVABLE)


def test_background_probe_failure_is_redacted_and_fails_closed() -> None:
    events: list[str] = []

    class FailingProbeBackground(_Background):
        def background_health(self) -> BackgroundHealth:
            raise RuntimeError("provider-token=raw-secret")

    resources = _resources(events, FailingProbeBackground(events))

    async def scenario() -> BackgroundHealth:
        async with resources.lifespan(FastAPI()):
            return resources.background_health()

    status = asyncio.run(scenario())
    assert status == BackgroundHealth(BackgroundHealthState.UNOBSERVABLE)
    assert "raw-secret" not in repr(status)


def test_failed_drain_does_not_close_resources_used_by_the_active_round() -> None:
    events: list[str] = []
    background = _Background(events, safe_to_close=False)
    resources = _resources(events, background)

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            with pytest.raises(RuntimeError, match="did not drain"):
                await resources.aclose()
            assert events == ["background-start", "background-stop"]
            background.permit_close()

    asyncio.run(scenario())

    assert events == [
        "background-start",
        "background-stop",
        "background-stop",
        "github",
        "jwks",
        "engine",
    ]


def test_failed_drain_can_be_retried_after_the_round_terminates() -> None:
    events: list[str] = []
    background = _Background(events, safe_to_close=False)
    resources = _resources(events, background)

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            with pytest.raises(RuntimeError, match="did not drain"):
                await resources.aclose()
            background.permit_close()
            await resources.aclose()

    asyncio.run(scenario())

    assert events == [
        "background-start",
        "background-stop",
        "background-stop",
        "github",
        "jwks",
        "engine",
    ]
    assert resources.is_open is False


def test_background_stop_exception_is_retryable_without_closing_dependencies() -> None:
    events: list[str] = []

    class FailingOnceBackground(_Background):
        def __init__(self) -> None:
            super().__init__(events)
            self._failures = 1

        async def stop(self) -> bool:
            events.append("background-stop")
            if self._failures:
                self._failures -= 1
                raise RuntimeError("provider-token=raw-secret")
            return True

    resources = _resources(events, FailingOnceBackground())

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            with pytest.raises(RuntimeError, match="did not drain") as captured:
                await resources.aclose()
            assert "raw-secret" not in str(captured.value)
            await resources.aclose()

    asyncio.run(scenario())

    assert events == [
        "background-start",
        "background-stop",
        "background-stop",
        "github",
        "jwks",
        "engine",
    ]


def test_concurrent_close_callers_share_one_finalizer_before_startup() -> None:
    events: list[str] = []
    resources = _resources(events, _Background(events))

    async def scenario() -> None:
        await asyncio.gather(resources.aclose(), resources.aclose())

    asyncio.run(scenario())

    assert events == ["github", "jwks", "engine"]


def test_partial_external_cleanup_retries_only_the_unfinished_resource() -> None:
    events: list[str] = []
    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, _Closeable(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks", failures=1)),
        cast(RuntimeBackgroundService, _Background(events)),
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await resources.aclose()
        await resources.aclose()

    asyncio.run(scenario())

    assert events == [
        "github",
        "jwks",
        "engine",
        "jwks",
    ]
    assert resources.is_open is False


def test_start_failure_still_closes_every_constructed_resource() -> None:
    events: list[str] = []
    resources = _resources(
        events,
        _Background(events, start_failure=RuntimeError("start failed")),
    )

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            raise AssertionError("unreachable")

    with pytest.raises(RuntimeError, match="start failed"):
        asyncio.run(scenario())

    assert events == [
        "background-start",
        "background-stop",
        "github",
        "jwks",
        "engine",
    ]


def test_start_failure_remains_primary_when_cleanup_also_fails() -> None:
    events: list[str] = []

    class SecretFailure(_Closeable):
        async def aclose(self) -> None:
            raise RuntimeError("provider-token=raw-secret")

    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, SecretFailure(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        cast(
            RuntimeBackgroundService,
            _Background(events, start_failure=RuntimeError("start failed")),
        ),
    )

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            raise AssertionError("unreachable")

    with pytest.raises(RuntimeError, match="start failed") as captured:
        asyncio.run(scenario())

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert captured.value.__notes__ == ["runtime resource cleanup also failed"]
    assert "raw-secret" not in rendered


def test_concurrent_close_during_startup_cannot_reactivate_the_runtime() -> None:
    events: list[str] = []
    startup_entered = asyncio.Event()
    startup_release = asyncio.Event()
    serving = False

    class BlockingStartBackground(_Background):
        async def start(self) -> None:
            events.append("background-start")
            startup_entered.set()
            await startup_release.wait()

    resources = _resources(events, BlockingStartBackground(events))

    async def scenario() -> None:
        nonlocal serving

        async def run_lifespan() -> None:
            nonlocal serving
            async with resources.lifespan(FastAPI()):
                serving = True

        startup = asyncio.create_task(run_lifespan())
        await startup_entered.wait()
        await resources.aclose()
        startup_release.set()
        with pytest.raises(RuntimeError, match="startup was interrupted"):
            await startup

    asyncio.run(scenario())

    assert serving is False
    assert resources.lifecycle_ready is False
    assert events == [
        "background-start",
        "background-stop",
        "github",
        "jwks",
        "engine",
    ]


def test_concurrent_close_during_resource_prepare_cannot_start_background() -> None:
    events: list[str] = []
    prepare_entered = asyncio.Event()
    serving = False

    class BlockingPrepare(_Closeable):
        async def prepare(self) -> None:
            events.append("keycloak-prepare")
            prepare_entered.set()
            await asyncio.Event().wait()

    resources = _resources(
        events,
        _Background(events),
        additional_resources=(BlockingPrepare(events, "keycloak"),),
    )

    async def scenario() -> None:
        nonlocal serving

        async def run_lifespan() -> None:
            nonlocal serving
            async with resources.lifespan(FastAPI()):
                serving = True

        startup = asyncio.create_task(run_lifespan())
        await prepare_entered.wait()
        await resources.aclose()
        with pytest.raises(RuntimeError, match="startup was interrupted"):
            await startup

    asyncio.run(scenario())

    assert serving is False
    assert resources.lifecycle_ready is False
    assert events == ["keycloak-prepare", "keycloak", "github", "jwks", "engine"]


@pytest.mark.parametrize(
    ("first_round", "expected_message"),
    [
        ("failure", "initial reconciliation failed"),
        ("timeout", "initial reconciliation timed out"),
    ],
)
def test_fastapi_rejects_lifespan_before_any_stateful_route_is_served(
    first_round: str,
    expected_message: str,
) -> None:
    events: list[str] = []
    route_calls = 0

    async def round_runner(abort_signal: asyncio.Event, /) -> None:
        if first_round == "failure":
            raise RuntimeError("provider-token=raw-secret")
        await abort_signal.wait()

    background = PeriodicReconciliationService(
        ReconciliationScheduler(round_runner),
        interval_seconds=60,
        startup_timeout_seconds=0.01,
        drain_timeout_seconds=0.1,
    )
    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, _Closeable(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        background,
    )
    app = FastAPI(lifespan=resources.lifespan)

    @app.get("/stateful")
    async def stateful() -> dict[str, bool]:
        nonlocal route_calls
        route_calls += 1
        return {"ok": True}

    with (
        pytest.raises(
            InitialReconciliationError,
            match=expected_message,
        ) as captured,
        TestClient(app),
    ):
        raise AssertionError("failed startup must not enter the serving context")

    assert route_calls == 0
    assert events == ["github", "jwks", "engine"]
    assert "raw-secret" not in "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )


def test_cleanup_has_one_owner_enforced_deadline() -> None:
    events: list[str] = []

    class BlockingCloseable(_Closeable):
        async def aclose(self) -> None:
            events.append("github")
            await asyncio.Event().wait()

    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, BlockingCloseable(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        shutdown_timeout_seconds=0.001,
    )

    with pytest.raises(RuntimeError, match="cleanup exceeded its deadline"):
        asyncio.run(resources.aclose())

    assert events == ["github"]
    assert resources.is_open is True
    assert resources.cleanup_pending is False


def test_concurrent_cleanup_callers_observe_the_same_owner_deadline() -> None:
    events: list[str] = []
    entered = asyncio.Event()

    class BlockingCloseable(_Closeable):
        async def aclose(self) -> None:
            events.append("github")
            entered.set()
            await asyncio.Event().wait()

    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, BlockingCloseable(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        shutdown_timeout_seconds=0.01,
    )

    async def scenario() -> tuple[BaseException | None, BaseException | None]:
        first = asyncio.create_task(resources.aclose())
        await entered.wait()
        second = asyncio.create_task(resources.aclose())
        results = await asyncio.gather(first, second, return_exceptions=True)
        return cast(tuple[BaseException | None, BaseException | None], tuple(results))

    first, second = asyncio.run(scenario())

    assert type(first) is RuntimeError
    assert type(second) is RuntimeError
    assert str(first) == str(second) == "runtime resource cleanup exceeded its deadline"
    assert events == ["github"]
    assert resources.is_open is True
    assert resources.cleanup_pending is False


def test_deadline_exposes_cleanup_pending_until_cancellation_settles() -> None:
    events: list[str] = []
    cancellation_observed = asyncio.Event()
    cancellation_release = asyncio.Event()
    cleanup_returned = asyncio.Event()

    class CancellationSuppressingCloseable(_Closeable):
        async def aclose(self) -> None:
            events.append("github")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()
                cancellation_observed.set()
                await cancellation_release.wait()
                cleanup_returned.set()

    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(
            GitHubAppTransportFactory,
            CancellationSuppressingCloseable(events, "github"),
        ),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        shutdown_timeout_seconds=0.001,
    )

    async def scenario() -> tuple[bool, bool]:
        with pytest.raises(RuntimeError, match="cleanup exceeded its deadline"):
            await resources.aclose()
        await cancellation_observed.wait()
        pending_before_settlement = resources.cleanup_pending
        cancellation_release.set()
        await cleanup_returned.wait()
        await asyncio.sleep(0)
        return pending_before_settlement, resources.cleanup_pending

    assert asyncio.run(scenario()) == (True, False)
    assert events == ["github"]
    assert resources.is_open is True


def test_caller_cancellation_does_not_cancel_the_cleanup_owner() -> None:
    events: list[str] = []

    class BlockingCloseable(_Closeable):
        def __init__(self) -> None:
            super().__init__(events, "github")
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def aclose(self) -> None:
            events.append("github")
            self.entered.set()
            await self.release.wait()

    github = BlockingCloseable()
    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, github),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
    )

    async def scenario() -> None:
        caller = asyncio.create_task(resources.aclose())
        await github.entered.wait()
        caller.cancel()
        github.release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller

    asyncio.run(scenario())

    assert events == ["github", "jwks", "engine"]
    assert resources.is_open is False


def test_cleanup_failure_traceback_does_not_retain_provider_diagnostics() -> None:
    events: list[str] = []

    class SecretFailure(_Closeable):
        async def aclose(self) -> None:
            raise RuntimeError("provider-token=raw-secret")

    resources = RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, SecretFailure(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
    )

    with pytest.raises(RuntimeError, match="runtime resource cleanup failed") as captured:
        asyncio.run(resources.aclose())

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "raw-secret" not in rendered


def _resources(
    events: list[str],
    background: RuntimeBackgroundService,
    *,
    additional_resources: tuple[RuntimeAsyncResource, ...] = (),
) -> RuntimeResources:
    return RuntimeResources(
        cast(AsyncEngine, _Engine(events)),
        cast(GitHubAppTransportFactory, _Closeable(events, "github")),
        cast(GitHubActionsJwksProvider, _Closeable(events, "jwks")),
        background,
        additional_resources=additional_resources,
    )


@pytest.mark.parametrize("phase", ["prepare", "first_round"])
@pytest.mark.parametrize("suppress_cancel", [False, True])
def test_readiness_activation_cannot_survive_interrupted_startup(
    phase: str, suppress_cancel: bool
) -> None:
    async def scenario() -> None:
        events: list[str] = []
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def block() -> None:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                if not suppress_cancel:
                    raise
                await release.wait()

        class Prepared(_Closeable):
            async def prepare(self) -> None:
                await super().prepare()
                if phase == "prepare":
                    await block()

        class Background(_Background):
            async def start(self) -> None:
                await super().start()
                if phase == "first_round":
                    await block()

        class Readiness:
            def activate(self) -> None:
                events.append("activate")

            def stop(self) -> None:
                events.append("revoke")

            async def drain(self) -> None:
                events.append("drain")

        resources = _resources(
            events, Background(events), additional_resources=(Prepared(events, "prepare"),)
        )
        resources.bind_readiness(Readiness())

        async def lifespan() -> None:
            async with resources.lifespan(FastAPI()):
                events.append("serve")

        startup = asyncio.create_task(lifespan())
        await entered.wait()
        closing = asyncio.create_task(resources.aclose())
        await cancelled.wait()
        assert events.count("revoke") == 1 and "activate" not in events
        if suppress_cancel:
            assert not closing.done() and "drain" not in events
        release.set()
        await closing
        with pytest.raises(RuntimeError, match="startup was interrupted"):
            await startup
        assert "activate" not in events and "serve" not in events
        assert events.index("revoke") < events.index("drain") < events.index("github")
        assert not resources.lifecycle_ready

    asyncio.run(scenario())


@pytest.mark.parametrize("start", [False, True])
def test_one_readiness_binding_activates_synchronously_and_drains_before_teardown(
    start: bool,
) -> None:
    async def scenario() -> None:
        events: list[str] = []
        resources = _resources(events, _Background(events))

        class Readiness:
            def activate(self) -> None:
                assert resources.lifecycle_ready
                events.append("activate")

            def stop(self) -> None:
                assert not resources.lifecycle_ready
                events.append("revoke")

            async def drain(self) -> None:
                events.append("drain")

        participant = Readiness()
        resources.bind_readiness(participant)
        with pytest.raises(RuntimeError, match="bound once"):
            resources.bind_readiness(participant)
        if start:
            async with resources.lifespan(FastAPI()):
                assert events == ["background-start", "activate"]
        else:
            await resources.aclose()
        await resources.aclose()
        assert events.count("activate") == int(start)
        assert events.count("revoke") == events.count("drain") == 1
        assert events.index("drain") < events.index("github")
        with pytest.raises(RuntimeError, match="bound once"):
            resources.bind_readiness(participant)

    asyncio.run(scenario())


def test_failed_readiness_drain_cannot_fall_through_to_external_cleanup() -> None:
    async def scenario() -> None:
        events: list[str] = []
        resources = _resources(events, _Background(events))

        class Readiness:
            def activate(self) -> None:
                pass

            def stop(self) -> None:
                pass

            async def drain(self) -> None:
                events.append("drain")
                raise RuntimeError("private close canary")

        resources.bind_readiness(Readiness())
        for _ in range(2):
            with pytest.raises(RuntimeError, match="runtime resource cleanup failed") as captured:
                await resources.aclose()
            assert "private" not in "".join(traceback.format_exception(captured.value))
            assert resources.is_open and not resources.lifecycle_ready
        assert events == ["drain", "drain"]

    asyncio.run(scenario())
