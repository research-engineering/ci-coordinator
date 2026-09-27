from __future__ import annotations

import asyncio
import json
import logging
import traceback
from io import StringIO
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
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
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


class _ManagedDatabaseProbe:
    def activate(self) -> None:
        pass

    def stop(self) -> None:
        pass

    async def drain(self) -> None:
        pass


class _DatabaseProbe(_ManagedDatabaseProbe):
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
            samples = prometheus_samples(metrics)
            assert samples[("ci_coordinator_reconciliation_background_healthy", ())] == 0
            assert samples[("ci_coordinator_reconciliation_background_terminal_failure", ())] == 1
            assert (
                samples[("ci_coordinator_reconciliation_background_terminal_failures_total", ())]
                == 1
            )
            assert samples[("ci_coordinator_ready", ())] == 0
            assert samples[("ci_coordinator_dependency_ready", (("dependency", "database"),))] == 1
            return first

    status = asyncio.run(scenario())
    assert status.unavailable_dependencies == ("background_reconciliation",)
    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_reconciliation_background_healthy", ())] == 0
    assert samples[("ci_coordinator_reconciliation_background_terminal_failure", ())] == 0
    assert samples[("ci_coordinator_reconciliation_background_terminal_failures_total", ())] == 1
    assert samples[("ci_coordinator_ready", ())] == 0
    assert ("ci_coordinator_dependency_ready", (("dependency", "database"),)) not in samples
    assert samples[("ci_coordinator_dependency_ready", (("dependency", "runtime_resources"),))] == 0


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

        class BlockingDatabaseProbe(_ManagedDatabaseProbe):
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

        class BlockingDatabaseProbe(_ManagedDatabaseProbe):
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

        class BlockingDatabaseProbe(_ManagedDatabaseProbe):
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

        class BlockingDatabaseProbe(_ManagedDatabaseProbe):
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


@pytest.mark.parametrize("stalled", ["database", "oidc_jwks"])
@pytest.mark.parametrize("completed", [False, True])
def test_deadline_exports_only_completed_facts(stalled: str, completed: bool) -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        for name in ("database", "oidc_jwks"):
            metrics.dependency_ready(name)
        resources = _resources(_Background())
        settled = asyncio.Event()

        class Database(_ManagedDatabaseProbe):
            async def check(self) -> DatabaseReadiness:
                if stalled == "database":
                    await asyncio.Event().wait()
                settled.set()
                return DatabaseReadiness(completed, "ready" if completed else "audit_chain_invalid")

        class Provider:
            async def probe(self) -> bool:
                if stalled == "oidc_jwks":
                    await asyncio.Event().wait()
                settled.set()
                return completed

        readiness = _readiness(
            resources,
            timeout_ms=50,
            database_probe=Database(),
            required_probes=(("oidc_jwks", Provider()),),
            metrics=metrics,
        )
        async with resources.lifespan(FastAPI()):
            pending = asyncio.create_task(readiness())
            await settled.wait()
            status = await pending
            assert not status.ready
            other = "oidc_jwks" if stalled == "database" else "database"
            samples = prometheus_samples(metrics)
            assert ("ci_coordinator_dependency_ready", (("dependency", stalled),)) not in samples
            assert samples[("ci_coordinator_dependency_ready", (("dependency", other),))] == int(
                completed
            )
            assert samples[("ci_coordinator_ready", ())] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["overload", "cancel", "stop"])
def test_wave_partial_final_and_interruption_samples(interruption: str) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        resources = _resources(_Background())
        metrics = RuntimeMetrics()

        class Provider:
            async def probe(self) -> bool:
                entered.set()
                await release.wait()
                return True

        readiness = _readiness(
            resources,
            timeout_ms=1_000,
            required_probes=(("oidc_jwks", Provider()),),
            metrics=metrics,
        )
        async with resources.lifespan(FastAPI()):
            waiters = [asyncio.create_task(readiness())]
            await entered.wait()
            readiness._timeout_ms = 10
            partial = await readiness()
            readiness._timeout_ms = 1_000
            assert partial.unavailable_dependencies == ("oidc_jwks",)
            for _ in range(7):
                waiters.append(asyncio.create_task(readiness()))
                await asyncio.sleep(0)
            before = {
                key: value
                for key, value in prometheus_samples(metrics).items()
                if key[0].startswith("ci_coordinator_")
            }
            if interruption == "overload":
                assert (await readiness()).unavailable_dependencies == ("readiness_admission",)
                assert {
                    key: value
                    for key, value in prometheus_samples(metrics).items()
                    if key[0].startswith("ci_coordinator_")
                } == before
            elif interruption == "cancel":
                waiters[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiters[0]
                assert {
                    key: value
                    for key, value in prometheus_samples(metrics).items()
                    if key[0].startswith("ci_coordinator_")
                } == before
            else:
                await resources.aclose()
                assert not (await readiness()).ready
            release.set()
            results = await asyncio.gather(*waiters, return_exceptions=True)
            samples = prometheus_samples(metrics)
            if interruption == "stop":
                assert not any(
                    isinstance(value, ReadinessStatus) and value.ready for value in results
                )
                assert (
                    "ci_coordinator_dependency_ready",
                    (("dependency", "database"),),
                ) not in samples
                assert (
                    "ci_coordinator_dependency_ready",
                    (("dependency", "oidc_jwks"),),
                ) not in samples
            else:
                assert all(value.ready for value in results if isinstance(value, ReadinessStatus))
                assert (
                    samples[("ci_coordinator_dependency_ready", (("dependency", "oidc_jwks"),))]
                    == 1
                )
                assert samples[("ci_coordinator_ready", ())] == 1

    asyncio.run(scenario())


def test_stopped_wave_cannot_overwrite_a_distinct_lifetime() -> None:
    async def scenario() -> None:
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        metrics = RuntimeMetrics()
        old_resources = _resources(_Background())
        new_resources = _resources(_Background())

        class LateDatabase(_ManagedDatabaseProbe):
            async def check(self) -> DatabaseReadiness:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                return DatabaseReadiness(True, "ready")

        old = _readiness(
            old_resources, database_probe=LateDatabase(), metrics=metrics, timeout_ms=1_000
        )
        new = _readiness(new_resources, metrics=metrics)
        async with old_resources.lifespan(FastAPI()):
            waiter = asyncio.create_task(old())
            await entered.wait()
            closing = asyncio.create_task(old_resources.aclose())
            await cancelled.wait()
            async with new_resources.lifespan(FastAPI()):
                assert (await new()).ready
                before = prometheus_samples(metrics)
                release.set()
                await asyncio.gather(waiter, return_exceptions=True)
                await closing
                assert not (await old()).ready
                after = prometheus_samples(metrics)
                assert {
                    key: value
                    for key, value in after.items()
                    if key[0].startswith("ci_coordinator_")
                } == {
                    key: value
                    for key, value in before.items()
                    if key[0].startswith("ci_coordinator_")
                }

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled_dependency", ["database", "oidc_jwks"])
def test_cancelled_child_joins_its_suspended_sibling(cancelled_dependency: str) -> None:
    async def scenario() -> None:
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        resources = _resources(_Background())

        async def outcome(name: str) -> bool:
            if name == cancelled_dependency:
                await entered.wait()
                raise asyncio.CancelledError
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                raise
            raise AssertionError("unreleased sibling returned")

        class Database(_ManagedDatabaseProbe):
            async def check(self) -> DatabaseReadiness:
                return DatabaseReadiness(await outcome("database"), "ready")

        class Provider:
            async def probe(self) -> bool:
                return await outcome("oidc_jwks")

        readiness = _readiness(
            resources,
            database_probe=Database(),
            required_probes=(("oidc_jwks", Provider()),),
            timeout_ms=1_000,
        )
        async with resources.lifespan(FastAPI()):
            pending = asyncio.create_task(readiness())
            await cancelled.wait()
            assert readiness._inflight is not None and not readiness._inflight.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending

    asyncio.run(scenario())


@pytest.mark.parametrize("available", [False, True])
def test_instrumentation_failure_preserves_completed_readiness(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
) -> None:
    metrics = RuntimeMetrics()

    def fail_labels(*_: str) -> None:
        raise RuntimeError("private metric canary")

    monkeypatch.setattr(metrics._dependency_ready, "labels", fail_labels)
    resources = _resources(_Background())
    readiness = _readiness(
        resources,
        metrics=metrics,
        database_probe=_DatabaseProbe(
            DatabaseReadiness(available, "ready" if available else "audit_chain_invalid")
        ),
    )

    async def scenario() -> None:
        async with resources.lifespan(FastAPI()):
            result = await readiness()
            assert result.ready is available
            assert result.unavailable_dependencies == (() if available else ("database",))
            assert "private" not in repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_caller", [False, True])
@pytest.mark.parametrize("cancel_child", [False, True])
def test_runtime_drain_distinguishes_caller_cancellation_when_wave_is_done(
    monkeypatch: pytest.MonkeyPatch,
    cancel_caller: bool,
    cancel_child: bool,
) -> None:
    async def scenario() -> None:
        entered, stopped, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        observed: list[asyncio.CancelledError] = []
        completion_cancellations: list[bool] = []
        draining: asyncio.Task[None] | None = None
        original_wait = asyncio.wait
        readiness = _readiness(_resources(_Background()))

        async def wave() -> ReadinessStatus:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                stopped.set()
                await release.wait()
            if cancel_child:
                raise asyncio.CancelledError("owned child")
            return ReadinessStatus(False, ("runtime_resources",))

        worker = asyncio.create_task(wave())
        readiness._inflight = worker
        await entered.wait()

        async def recording_wait(
            tasks: set[asyncio.Task[object]],
        ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
            try:
                return await original_wait(tasks)
            except asyncio.CancelledError as error:
                if asyncio.current_task() is draining:
                    assert worker.done()
                    observed.append(error)
                raise

        def on_done(_task: object) -> None:
            assert draining is not None
            if cancel_caller:
                completion_cancellations.append(draining.cancel("drain caller"))

        monkeypatch.setattr(asyncio, "wait", recording_wait)
        try:
            worker.add_done_callback(on_done)
            draining = asyncio.create_task(readiness.drain())
            await stopped.wait()
            assert not worker.done() and not draining.done()
            release.set()
            if cancel_caller:
                with pytest.raises(asyncio.CancelledError) as captured:
                    await draining
                assert observed == [captured.value]
                assert captured.value is observed[0]
                assert completion_cancellations == [True]
            else:
                await draining
                assert observed == []
                assert completion_cancellations == []
            assert worker.done() and worker.cancelled() is cancel_child
            await readiness.drain()
        finally:
            release.set()
            await asyncio.gather(
                worker, *(() if draining is None else (draining,)), return_exceptions=True
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_close", [False, True])
def test_runtime_drain_preserves_first_cancellation_across_database_drain(
    monkeypatch: pytest.MonkeyPatch,
    failed_close: bool,
) -> None:
    async def scenario() -> None:
        entered, stopped, release, cancelled = (asyncio.Event() for _ in range(4))
        observed: list[asyncio.CancelledError] = []
        draining: asyncio.Task[None] | None = None
        original_wait = asyncio.wait
        failure = RuntimeError("private-close-canary")
        stop_calls = drain_calls = 0

        class Database(_DatabaseProbe):
            def stop(self) -> None:
                nonlocal stop_calls
                stop_calls += 1

            async def drain(self) -> None:
                nonlocal drain_calls
                drain_calls += 1
                if failed_close:
                    raise failure

        readiness = _readiness(
            _resources(_Background()), database_probe=Database(DatabaseReadiness(True, "ready"))
        )

        async def wave() -> ReadinessStatus:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                stopped.set()
                await release.wait()
            return ReadinessStatus(False, ("runtime_resources",))

        worker = asyncio.create_task(wave())
        readiness._inflight = worker
        await entered.wait()

        async def recording_wait(
            tasks: set[asyncio.Task[object]],
        ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
            try:
                return await original_wait(tasks)
            except asyncio.CancelledError as error:
                if asyncio.current_task() is draining:
                    assert not worker.done()
                    observed.append(error)
                    cancelled.set()
                raise

        monkeypatch.setattr(asyncio, "wait", recording_wait)
        try:
            draining = asyncio.create_task(readiness.drain())
            await stopped.wait()
            assert draining.cancel("first drain caller")
            async with asyncio.timeout(1):
                await cancelled.wait()
            assert not draining.done() and len(observed) == 1
            release.set()
            with pytest.raises(asyncio.CancelledError) as captured:
                await draining
            assert captured.value is observed[0]
            assert getattr(captured.value, "__notes__", []) == (
                ["database readiness drain also failed"] if failed_close else []
            )
            assert "private-close-canary" not in "".join(traceback.format_exception(captured.value))
            assert worker.done() and (stop_calls, drain_calls) == (1, 1)
            if failed_close:
                with pytest.raises(RuntimeError) as later:
                    await readiness.drain()
                assert later.value is failure
            else:
                await readiness.drain()
            assert (stop_calls, drain_calls) == (1, 2)
        finally:
            release.set()
            await asyncio.gather(
                worker, *(() if draining is None else (draining,)), return_exceptions=True
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("dependency", ["database", "oidc_jwks"])
@pytest.mark.parametrize("failure", ["unowned_timeout", "owned_expiry", "unexpected"])
def test_readiness_timeout_provenance_controls_sample_and_diagnostic(
    dependency: str,
    failure: str,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        output = StringIO()
        logger = logging.Logger("readiness-timeout-origin")
        logger.addHandler(logging.StreamHandler(output))
        metrics = RuntimeMetrics()
        metrics.dependency_ready(dependency)
        resources = _resources(_Background())

        async def observe(name: str) -> bool:
            if name != dependency:
                return True
            entered.set()
            if failure == "owned_expiry":
                await asyncio.Event().wait()
            if failure == "unowned_timeout":
                raise TimeoutError("private-probe-canary")
            raise RuntimeError("private-probe-canary")

        class Database(_ManagedDatabaseProbe):
            async def check(self) -> DatabaseReadiness:
                return DatabaseReadiness(await observe("database"), "ready")

        class Provider:
            async def probe(self) -> bool:
                return await observe("oidc_jwks")

        readiness = RuntimeReadiness(
            resources=resources,
            database_probe=Database(),
            timeout_ms=50,
            required_probes=(("oidc_jwks", Provider()),),
            metrics=metrics,
            diagnostics=RuntimeDiagnosticObserver(StructuredEventLogger(logger)),
        )
        async with resources.lifespan(FastAPI()):
            waiter = asyncio.create_task(readiness())
            await entered.wait()
            status = await waiter
            assert not status.ready and status.unavailable_dependencies == (dependency,)
            samples = prometheus_samples(metrics)
            sample = ("ci_coordinator_dependency_ready", (("dependency", dependency),))
            if failure == "owned_expiry":
                assert sample not in samples
            else:
                assert samples[sample] == 0
            peer = "oidc_jwks" if dependency == "database" else "database"
            assert samples[("ci_coordinator_dependency_ready", (("dependency", peer),))] == 1
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            assert len(records) == (0 if failure == "owned_expiry" else 1)
            if records:
                assert records[0]["stage"] == (
                    "readiness_database" if dependency == "database" else "readiness_dependency"
                )
                assert records[0]["exceptionType"] == (
                    "TimeoutError" if failure == "unowned_timeout" else "RuntimeError"
                )
            assert "private-probe-canary" not in output.getvalue()

    asyncio.run(scenario())


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
