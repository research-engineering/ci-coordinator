from __future__ import annotations

import asyncio
import traceback
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.integrations import GitHubActionsJwksProvider
from ci_coordinator.integrations.github import GitHubAppTransportFactory
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
