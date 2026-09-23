from __future__ import annotations

import asyncio
from time import monotonic
from typing import cast

import pytest
from fastapi import FastAPI
from prometheus_support import prometheus_samples
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.integrations import GitHubActionsJwksProvider
from ci_coordinator.integrations.github import GitHubAppTransportFactory
from ci_coordinator.observability import (
    BackgroundHealth,
    BackgroundHealthState,
    ReadinessStatus,
    RuntimeMetrics,
)
from ci_coordinator.persistence import DatabaseReadiness
from ci_coordinator.runtime.readiness import (
    DatabaseStatusProbe,
    RuntimeAvailabilityProbe,
    RuntimeReadiness,
)
from ci_coordinator.runtime.resources import RuntimeBackgroundService, RuntimeResources


class _Closeable:
    async def aclose(self) -> None:
        pass


class _Engine:
    async def dispose(self) -> None:
        pass


class _Background:
    def __init__(self, state: BackgroundHealthState = BackgroundHealthState.HEALTHY) -> None:
        self.state = state

    async def start(self) -> None:
        pass

    async def stop(self) -> bool:
        return True

    def background_health(self) -> BackgroundHealth:
        return BackgroundHealth(self.state)


class _Probe:
    def __init__(self, result: bool | BaseException) -> None:
        self._result = result

    async def probe(self) -> bool:
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _DatabaseProbe:
    def __init__(
        self,
        result: DatabaseReadiness | BaseException,
        *,
        delay_seconds: float = 0,
    ) -> None:
        self._result = result
        self._delay_seconds = delay_seconds

    async def check(self) -> DatabaseReadiness:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result

    def set_result(self, result: DatabaseReadiness | BaseException) -> None:
        self._result = result


def test_readiness_requires_active_resources_database_and_healthy_background() -> None:
    resources = _resources(_Background())
    readiness = _readiness(
        resources,
        database_probe=_DatabaseProbe(DatabaseReadiness(True, "ready")),
    )

    async def scenario() -> tuple[ReadinessStatus, ReadinessStatus, ReadinessStatus]:
        before = await readiness()
        async with resources.lifespan(FastAPI()):
            active = await readiness()
        after = await readiness()
        return before, active, after

    before, active, after = asyncio.run(scenario())
    assert before.ready is False
    assert before.unavailable_dependencies == (
        "background_reconciliation",
        "runtime_resources",
    )
    assert active.ready is True
    assert active.unavailable_dependencies == ()
    assert after == before


def test_terminal_background_failure_blocks_readiness_and_updates_bounded_metrics() -> None:
    resources = _resources(_Background(BackgroundHealthState.TERMINAL_FAILURE))
    metrics = RuntimeMetrics()
    readiness = _readiness(
        resources,
        metrics=metrics,
        database_probe=_DatabaseProbe(DatabaseReadiness(True, "ready")),
    )

    async def scenario() -> ReadinessStatus:
        async with resources.lifespan(FastAPI()):
            first = await readiness()
            second = await readiness()
            assert first == second
            return first

    status = asyncio.run(scenario())
    assert status.unavailable_dependencies == ("background_reconciliation",)
    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_reconciliation_background_healthy", ())] == 0
    assert samples[("ci_coordinator_reconciliation_background_terminal_failure", ())] == 1
    assert samples[("ci_coordinator_reconciliation_background_terminal_failures_total", ())] == 1
    assert samples[("ci_coordinator_ready", ())] == 0
    assert samples[("ci_coordinator_dependency_ready", (("dependency", "database"),))] == 1


def test_readiness_timeout_is_bounded_and_redacts_database_failure() -> None:
    resources = _resources(_Background())
    readiness = _readiness(
        resources,
        timeout_ms=10,
        database_probe=_DatabaseProbe(DatabaseReadiness(True, "ready"), delay_seconds=60),
    )

    async def scenario() -> tuple[ReadinessStatus, float]:
        async with resources.lifespan(FastAPI()):
            started = monotonic()
            status = await readiness()
            return status, monotonic() - started

    status, elapsed = asyncio.run(scenario())
    assert status.unavailable_dependencies == ("database",)
    assert elapsed < 0.5
    assert "unreachable" not in repr(status)


def test_unexpected_database_error_is_redacted_and_cancellation_propagates() -> None:
    resources = _resources(_Background())
    database = _DatabaseProbe(RuntimeError("postgresql://operator:secret@example.invalid/database"))
    readiness = _readiness(resources, database_probe=database)

    async def scenario() -> ReadinessStatus:
        async with resources.lifespan(FastAPI()):
            status = await readiness()
            assert "secret" not in repr(status)
            database.set_result(asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError):
                await readiness()
            return status

    status = asyncio.run(scenario())
    assert status.unavailable_dependencies == ("database",)


def test_required_runtime_probe_blocks_readiness_but_redacts_its_failure() -> None:
    resources = _resources(_Background())
    readiness = _readiness(
        resources,
        required_probes=(("oidc_jwks", _Probe(RuntimeError("secret token"))),),
        database_probe=_DatabaseProbe(DatabaseReadiness(True, "ready")),
    )

    async def scenario() -> ReadinessStatus:
        async with resources.lifespan(FastAPI()):
            return await readiness()

    result = asyncio.run(scenario())
    assert result.unavailable_dependencies == ("oidc_jwks",)
    assert "secret" not in repr(result)


def test_required_runtime_probe_cancellation_propagates() -> None:
    resources = _resources(_Background())
    readiness = _readiness(
        resources,
        required_probes=(("oidc_jwks", _Probe(asyncio.CancelledError())),),
        database_probe=_DatabaseProbe(DatabaseReadiness(True, "ready")),
    )

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            with pytest.raises(asyncio.CancelledError):
                await readiness()

    asyncio.run(scenario())


def test_readiness_linearizes_background_health_after_async_probes() -> None:
    async def scenario() -> ReadinessStatus:
        entered = asyncio.Event()
        release = asyncio.Event()
        background = _Background()
        resources = _resources(background)

        class BlockingDatabaseProbe:
            async def check(self) -> DatabaseReadiness:
                entered.set()
                await release.wait()
                return DatabaseReadiness(True, "ready")

        readiness = _readiness(resources, database_probe=BlockingDatabaseProbe())
        async with resources.lifespan(FastAPI()):
            pending = asyncio.create_task(readiness())
            await entered.wait()
            background.state = BackgroundHealthState.DEGRADED
            release.set()
            return await pending

    status = asyncio.run(scenario())

    assert status.ready is False
    assert status.unavailable_dependencies == ("background_reconciliation",)


def test_concurrent_runtime_readiness_callers_share_one_dependency_wave() -> None:
    async def scenario() -> tuple[int, tuple[ReadinessStatus, ...]]:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        class BlockingDatabaseProbe:
            async def check(self) -> DatabaseReadiness:
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                return DatabaseReadiness(True, "ready")

        resources = _resources(_Background())
        readiness = _readiness(resources, database_probe=BlockingDatabaseProbe())
        async with resources.lifespan(FastAPI()):
            waiters = tuple(asyncio.create_task(readiness()) for _ in range(6))
            await entered.wait()
            await asyncio.sleep(0)
            release.set()
            return calls, tuple(await asyncio.gather(*waiters))

    calls, results = asyncio.run(scenario())

    assert calls == 1
    assert all(result.ready for result in results)


def test_cancelled_runtime_readiness_waiter_does_not_cancel_shared_wave() -> None:
    async def scenario() -> tuple[int, ReadinessStatus]:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        class BlockingDatabaseProbe:
            async def check(self) -> DatabaseReadiness:
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                return DatabaseReadiness(True, "ready")

        resources = _resources(_Background())
        readiness = _readiness(resources, database_probe=BlockingDatabaseProbe())
        async with resources.lifespan(FastAPI()):
            cancelled = asyncio.create_task(readiness())
            surviving = asyncio.create_task(readiness())
            await entered.wait()
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            release.set()
            return calls, await surviving

    calls, status = asyncio.run(scenario())

    assert calls == 1
    assert status.ready


def test_runtime_readiness_waiter_budget_rejects_without_starting_another_wave() -> None:
    async def scenario() -> tuple[int, ReadinessStatus]:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        class BlockingDatabaseProbe:
            async def check(self) -> DatabaseReadiness:
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                return DatabaseReadiness(True, "ready")

        resources = _resources(_Background())
        readiness = _readiness(resources, database_probe=BlockingDatabaseProbe())
        async with resources.lifespan(FastAPI()):
            waiters = []
            for _ in range(8):
                waiters.append(asyncio.create_task(readiness()))
                await asyncio.sleep(0)
            await entered.wait()
            rejected = await readiness()
            release.set()
            await asyncio.gather(*waiters)
            return calls, rejected

    calls, rejected = asyncio.run(scenario())

    assert calls == 1
    assert rejected.unavailable_dependencies == ("readiness_admission",)


@pytest.mark.parametrize("timeout_ms", (0, -1, True, 1.5))
def test_readiness_rejects_invalid_timeout(timeout_ms: object) -> None:
    with pytest.raises(ValueError, match="positive milliseconds"):
        _readiness(_resources(_Background()), timeout_ms=timeout_ms)  # type: ignore[arg-type]


def _resources(background: object) -> RuntimeResources:
    return RuntimeResources(
        cast(AsyncEngine, _Engine()),
        cast(GitHubAppTransportFactory, _Closeable()),
        cast(GitHubActionsJwksProvider, _Closeable()),
        cast(RuntimeBackgroundService, background),
    )


def _readiness(
    resources: RuntimeResources,
    *,
    timeout_ms: int = 100,
    required_probes: tuple[tuple[str, RuntimeAvailabilityProbe], ...] = (),
    metrics: RuntimeMetrics | None = None,
    database_probe: DatabaseStatusProbe | None = None,
) -> RuntimeReadiness:
    return RuntimeReadiness(
        resources=resources,
        timeout_ms=timeout_ms,
        required_probes=required_probes,
        metrics=metrics,
        database_probe=database_probe or _DatabaseProbe(DatabaseReadiness(True, "ready")),
    )
