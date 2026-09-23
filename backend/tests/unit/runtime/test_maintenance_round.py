from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from io import StringIO

import pytest
from prometheus_support import prometheus_samples

from ci_coordinator.observability import (
    MaintenanceOperationName,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
)
from ci_coordinator.runtime.maintenance_round import (
    BoundedMaintenanceOperation,
    RuntimeMaintenanceRound,
)


@dataclass
class _StepClock:
    value: float = 0.0

    def now(self) -> float:
        self.value += 0.25
        return self.value


@pytest.mark.parametrize(
    "operation",
    ["ci_economics_collection", "ci_observation_discovery", "ci_observation_gap_cleanup"],
)
def test_initial_round_preserves_reconciliation_as_the_only_startup_authority(
    operation: MaintenanceOperationName,
) -> None:
    calls: list[str] = []

    async def primary(_: asyncio.Event) -> None:
        calls.append("primary")

    async def telemetry(_: asyncio.Event) -> None:
        calls.append("telemetry")

    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation(operation, 1, telemetry),),
        metrics=RuntimeMetrics(),
        clock=_StepClock(),
    )

    asyncio.run(runtime(asyncio.Event()))

    assert calls == ["primary"]


def test_non_authoritative_operations_run_concurrently_after_startup() -> None:
    calls: list[str] = []
    release_first = asyncio.Event()
    metrics = RuntimeMetrics()

    async def primary(_: asyncio.Event) -> None:
        calls.append("primary")

    async def first(_: asyncio.Event) -> None:
        calls.append("first-started")
        await release_first.wait()
        calls.append("first-finished")

    async def second(_: asyncio.Event) -> None:
        calls.append("second-started")
        release_first.set()

    runtime = RuntimeMaintenanceRound(
        primary,
        (
            BoundedMaintenanceOperation("ci_economics_collection", 1, first),
            BoundedMaintenanceOperation("ci_economics_expiry", 1, second),
        ),
        metrics=metrics,
        clock=_StepClock(),
    )

    async def exercise() -> None:
        abort_signal = asyncio.Event()
        await runtime(abort_signal)
        await runtime(abort_signal)

    asyncio.run(exercise())

    assert calls == ["primary", "primary", "first-started", "second-started", "first-finished"]
    samples = prometheus_samples(metrics)
    assert _maintenance_count(samples, "ci_economics_collection", "succeeded") == 1
    assert _maintenance_count(samples, "ci_economics_expiry", "succeeded") == 1


@pytest.mark.parametrize("primary_result", ["failure", "failure-with-live-child", "waiting"])
def test_later_maintenance_does_not_require_primary_success(
    primary_result: str,
    diagnostic_output: tuple[RuntimeDiagnosticObserver, StringIO],
) -> None:
    async def exercise() -> None:
        completed = asyncio.Event()
        maintenance_started, release_maintenance = asyncio.Event(), asyncio.Event()
        primary_failed = asyncio.Event()
        calls = 0
        error = RuntimeError("private primary failure")

        async def primary(_: asyncio.Event) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return
            if primary_result == "failure-with-live-child":
                await maintenance_started.wait()
            if primary_result != "waiting":
                primary_failed.set()
                raise error
            await completed.wait()

        async def maintenance(_: asyncio.Event) -> None:
            maintenance_started.set()
            if primary_result == "failure-with-live-child":
                await release_maintenance.wait()
            completed.set()

        diagnostics, output = diagnostic_output
        metrics = RuntimeMetrics()
        runtime = RuntimeMaintenanceRound(
            primary,
            (BoundedMaintenanceOperation("ci_economics_expiry", 1, maintenance),),
            metrics=metrics,
            clock=_StepClock(),
            diagnostics=diagnostics,
        )
        async with asyncio.timeout(3):
            await runtime(asyncio.Event())
            assert not completed.is_set()
            if primary_result == "failure-with-live-child":

                async def run_expected_failure() -> None:
                    with pytest.raises(RuntimeError) as raised:
                        await runtime(asyncio.Event())
                    assert raised.value is error

                async with asyncio.TaskGroup() as group:
                    pending = group.create_task(run_expected_failure())
                    try:
                        await maintenance_started.wait()
                        await primary_failed.wait()
                        assert json.loads(output.getvalue())["stage"] == "reconciliation_round"
                        assert not pending.done()
                        assert not completed.is_set()
                    finally:
                        release_maintenance.set()
                assert json.loads(output.getvalue())["stage"] == "reconciliation_round"
            elif primary_result == "failure":
                with pytest.raises(RuntimeError) as raised:
                    await runtime(asyncio.Event())
                assert raised.value is error
                assert json.loads(output.getvalue())["stage"] == "reconciliation_round"
            else:
                await runtime(asyncio.Event())
                assert output.getvalue() == ""
        assert completed.is_set()
        assert calls == 2
        assert (
            _maintenance_count(prometheus_samples(metrics), "ci_economics_expiry", "succeeded") == 1
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("cancellation", ["external", "primary"])
def test_later_round_cancellation_drains_primary_and_maintenance(
    cancellation: str,
    diagnostic_output: tuple[RuntimeDiagnosticObserver, StringIO],
) -> None:
    async def exercise() -> None:
        primary_started, maintenance_started = asyncio.Event(), asyncio.Event()
        primary_stopped, maintenance_stopped = asyncio.Event(), asyncio.Event()
        calls = 0

        async def primary(_: asyncio.Event) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return
            primary_started.set()
            try:
                await maintenance_started.wait()
                if cancellation == "primary":
                    raise asyncio.CancelledError
                await asyncio.Event().wait()
            finally:
                primary_stopped.set()

        async def maintenance(_: asyncio.Event) -> None:
            maintenance_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                maintenance_stopped.set()

        diagnostics, output = diagnostic_output
        metrics = RuntimeMetrics()
        runtime = RuntimeMaintenanceRound(
            primary,
            (BoundedMaintenanceOperation("ci_economics_expiry", 30, maintenance),),
            metrics=metrics,
            clock=_StepClock(),
            diagnostics=diagnostics,
        )
        async with asyncio.timeout(3) as watchdog:
            await runtime(asyncio.Event())
            async with asyncio.TaskGroup() as group:
                pending = group.create_task(runtime(asyncio.Event()))
                await primary_started.wait()
                await maintenance_started.wait()
                if cancellation == "external":
                    pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
        assert not watchdog.expired()
        assert primary_stopped.is_set()
        assert maintenance_stopped.is_set()
        assert output.getvalue() == ""
        samples = prometheus_samples(metrics)
        for result in ("succeeded", "failed", "timed_out"):
            assert _maintenance_count(samples, "ci_economics_expiry", result) == 0

    asyncio.run(exercise())


def test_preexisting_abort_starts_no_later_maintenance() -> None:
    calls: list[str] = []

    async def primary(abort: asyncio.Event) -> None:
        calls.append("aborted-primary" if abort.is_set() else "initial-primary")

    async def maintenance(_: asyncio.Event) -> None:
        calls.append("maintenance")

    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation("ci_economics_expiry", 1, maintenance),),
        metrics=RuntimeMetrics(),
        clock=_StepClock(),
    )

    async def exercise() -> None:
        abort = asyncio.Event()
        await runtime(abort)
        abort.set()
        await runtime(abort)

    asyncio.run(exercise())
    assert calls == ["initial-primary", "aborted-primary"]


@pytest.fixture
def diagnostic_output() -> tuple[RuntimeDiagnosticObserver, StringIO]:
    output = StringIO()
    logger = logging.Logger("maintenance-diagnostic-test")
    logger.addHandler(logging.StreamHandler(output))
    return RuntimeDiagnosticObserver(StructuredEventLogger(logger)), output


@pytest.mark.parametrize(
    "operation",
    [
        "ci_economics_collection",
        "ci_economics_expiry",
        "ci_economics_observation_cleanup",
        "ci_economics_tombstone_purge",
    ],
)
@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError])
def test_operation_failure_is_contained_and_observable(
    operation: MaintenanceOperationName,
    error_type: type[Exception],
    diagnostic_output: tuple[RuntimeDiagnosticObserver, StringIO],
) -> None:
    async def primary(_: asyncio.Event) -> None:
        pass

    async def failing(_: asyncio.Event) -> None:
        raise error_type("bounded failure")

    metrics = RuntimeMetrics()
    diagnostics, output = diagnostic_output
    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation(operation, 1, failing),),
        metrics=metrics,
        clock=_StepClock(),
        diagnostics=diagnostics,
    )

    async def exercise() -> None:
        abort_signal = asyncio.Event()
        await runtime(abort_signal)
        await runtime(abort_signal)

    asyncio.run(exercise())

    samples = prometheus_samples(metrics)
    assert _maintenance_count(samples, operation, "failed") == 1
    assert _maintenance_count(samples, operation, "timed_out") == 0
    record = json.loads(output.getvalue())
    assert record["event"] == "unexpected_failure"
    assert record["exceptionType"] == error_type.__name__
    assert record["stage"] == operation
    assert "bounded failure" not in output.getvalue()


def test_expired_operation_deadline_is_not_an_unexpected_failure(
    diagnostic_output: tuple[RuntimeDiagnosticObserver, StringIO],
) -> None:
    cancelled = asyncio.Event()

    async def primary(_: asyncio.Event) -> None:
        pass

    async def waiting(_: asyncio.Event) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    diagnostics, output = diagnostic_output
    metrics = RuntimeMetrics()
    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation("ci_economics_collection", 1, waiting),),
        metrics=metrics,
        clock=_StepClock(),
        diagnostics=diagnostics,
    )

    async def exercise() -> None:
        async with asyncio.timeout(3):
            abort_signal = asyncio.Event()
            await runtime(abort_signal)
            await runtime(abort_signal)

    asyncio.run(exercise())

    assert cancelled.is_set()
    samples = prometheus_samples(metrics)
    assert _maintenance_count(samples, "ci_economics_collection", "timed_out") == 1
    assert _maintenance_count(samples, "ci_economics_collection", "failed") == 0
    assert output.getvalue() == ""


@pytest.mark.parametrize("error", [RuntimeError("private primary error"), asyncio.CancelledError()])
def test_primary_failure_propagates_without_becoming_startup_success(
    error: BaseException,
    diagnostic_output: tuple[RuntimeDiagnosticObserver, StringIO],
) -> None:
    calls: list[str] = []

    async def primary(_: asyncio.Event) -> None:
        calls.append("primary")
        if len(calls) <= 2:
            raise error

    async def maintenance(_: asyncio.Event) -> None:
        calls.append("maintenance")

    diagnostics, output = diagnostic_output
    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation("ci_economics_expiry", 1, maintenance),),
        metrics=RuntimeMetrics(),
        clock=_StepClock(),
        diagnostics=diagnostics,
    )

    async def exercise() -> None:
        for _ in range(2):
            with pytest.raises(type(error)) as raised:
                await runtime(asyncio.Event())
            assert raised.value is error
        assert calls == ["primary", "primary"]
        await runtime(asyncio.Event())
        assert calls == ["primary", "primary", "primary"]
        await runtime(asyncio.Event())

    asyncio.run(exercise())
    assert calls == ["primary", "primary", "primary", "primary", "maintenance"]
    if isinstance(error, asyncio.CancelledError):
        assert output.getvalue() == ""
    else:
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert len(records) == 2
        assert all(record["stage"] == "reconciliation_round" for record in records)
        assert all(record["exceptionType"] == "RuntimeError" for record in records)
        assert "private primary error" not in output.getvalue()


def test_cancelling_maintenance_is_not_a_failed_operation(
    diagnostic_output: tuple[RuntimeDiagnosticObserver, StringIO],
) -> None:
    async def primary(_: asyncio.Event) -> None:
        pass

    started = asyncio.Event()

    async def maintenance(_: asyncio.Event) -> None:
        started.set()
        await asyncio.Event().wait()

    diagnostics, output = diagnostic_output
    metrics = RuntimeMetrics()
    runtime = RuntimeMaintenanceRound(
        primary,
        (BoundedMaintenanceOperation("ci_economics_collection", 30, maintenance),),
        metrics=metrics,
        clock=_StepClock(),
        diagnostics=diagnostics,
    )

    async def exercise() -> None:
        await runtime(asyncio.Event())
        async with asyncio.TaskGroup() as group:
            task = group.create_task(runtime(asyncio.Event()))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(exercise())

    assert output.getvalue() == ""
    samples = prometheus_samples(metrics)
    assert _maintenance_count(samples, "ci_economics_collection", "failed") == 0
    assert _maintenance_count(samples, "ci_economics_collection", "timed_out") == 0


def _maintenance_count(
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    operation: str,
    result: str,
) -> float:
    return samples[
        (
            "ci_coordinator_maintenance_operations_total",
            (("operation", operation), ("result", result)),
        )
    ]
