from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from io import StringIO

import pytest
from prometheus_support import prometheus_samples

from ci_coordinator.observability import (
    BackgroundHealthState,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
)
from ci_coordinator.reconciliation import ReconciliationScheduler
from ci_coordinator.runtime.reconciliation_service import (
    InitialReconciliationError,
    PeriodicReconciliationService,
)


class _ScriptedRound:
    def __init__(self, outcomes: list[BaseException | asyncio.Event | None]) -> None:
        self._outcomes = outcomes
        self.calls = 0
        self.started = asyncio.Event()
        self.call_events: list[asyncio.Event] = []

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        call_event = asyncio.Event()
        self.call_events.append(call_event)
        self.calls += 1
        self.started.set()
        call_event.set()
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, asyncio.Event):
            abort_waiter = asyncio.create_task(abort_signal.wait())
            release_waiter = asyncio.create_task(outcome.wait())
            await asyncio.wait(
                (abort_waiter, release_waiter),
                return_when=asyncio.FIRST_COMPLETED,
            )
            abort_waiter.cancel()
            release_waiter.cancel()


def _service(
    round_runner: _ScriptedRound,
    *,
    interval_seconds: float = 60,
    startup_timeout_seconds: float = 1,
    drain_timeout_seconds: float = 1,
    metrics: RuntimeMetrics | None = None,
    diagnostics: RuntimeDiagnosticObserver | None = None,
) -> PeriodicReconciliationService:
    return PeriodicReconciliationService(
        ReconciliationScheduler(round_runner),
        interval_seconds=interval_seconds,
        startup_timeout_seconds=startup_timeout_seconds,
        drain_timeout_seconds=drain_timeout_seconds,
        metrics=metrics,
        diagnostics=diagnostics,
    )


async def _wait_until(predicate: Callable[[], bool], *, attempts: int = 500) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition was not observed within its test bound")


def test_start_waits_for_the_initial_round_and_drains_before_close() -> None:
    async def scenario() -> tuple[bool, int, bool]:
        release = asyncio.Event()
        round_runner = _ScriptedRound([release])
        service = _service(round_runner)
        startup = asyncio.create_task(service.start())
        await round_runner.started.wait()
        pending_before_terminal = not startup.done()
        release.set()
        await startup
        return pending_before_terminal, round_runner.calls, await service.stop()

    assert asyncio.run(scenario()) == (True, 1, True)


def test_startup_failure_rejects_startup_and_is_redacted() -> None:
    async def scenario() -> tuple[str, bool]:
        service = _service(_ScriptedRound([RuntimeError("provider-token=raw-secret")]))
        with pytest.raises(InitialReconciliationError) as captured:
            await service.start()
        return str(captured.value), await service.stop()

    assert asyncio.run(scenario()) == ("initial reconciliation failed", True)


def test_startup_timeout_is_bounded_and_the_round_is_drained() -> None:
    async def scenario() -> tuple[str, bool]:
        service = _service(
            _ScriptedRound([asyncio.Event()]),
            startup_timeout_seconds=0.001,
        )
        with pytest.raises(InitialReconciliationError) as captured:
            await service.start()
        return str(captured.value), await service.stop()

    assert asyncio.run(scenario()) == ("initial reconciliation timed out", True)


def test_stop_before_initial_publication_cannot_admit_startup() -> None:
    async def scenario() -> tuple[str, bool]:
        round_runner = _ScriptedRound([asyncio.Event()])
        service = _service(round_runner)
        startup = asyncio.create_task(service.start())
        await round_runner.started.wait()
        stopping = asyncio.create_task(service.stop())
        with pytest.raises(InitialReconciliationError) as captured:
            await startup
        return str(captured.value), await stopping

    assert asyncio.run(scenario()) == ("initial reconciliation failed", True)


def test_failed_post_start_round_degrades_until_a_successful_round_recovers() -> None:
    async def scenario() -> tuple[BackgroundHealthState, BackgroundHealthState, float]:
        third_release = asyncio.Event()
        round_runner = _ScriptedRound([None, RuntimeError("provider poll failed"), third_release])
        metrics = RuntimeMetrics()
        service = _service(round_runner, interval_seconds=0.001, metrics=metrics)
        await service.start()
        await _wait_until(
            lambda: service.background_health().state is BackgroundHealthState.DEGRADED
        )
        degraded = service.background_health().state
        await _wait_until(lambda: round_runner.calls >= 3)
        third_release.set()
        await _wait_until(
            lambda: service.background_health().state is BackgroundHealthState.HEALTHY
        )
        recovered = service.background_health().state
        failures = prometheus_samples(metrics)[
            ("ci_coordinator_reconciliation_rounds_total", (("result", "failed"),))
        ]
        assert await service.stop() is True
        return degraded, recovered, failures

    assert asyncio.run(scenario()) == (
        BackgroundHealthState.DEGRADED,
        BackgroundHealthState.HEALTHY,
        1,
    )


def test_interval_starts_after_the_previous_round_reaches_terminal_state() -> None:
    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        release = asyncio.Event()
        round_runner = _ScriptedRound([release, None])
        service = _service(round_runner, interval_seconds=0.05)
        startup = asyncio.create_task(service.start())
        await round_runner.started.wait()
        release.set()
        await startup
        terminal_at = loop.time()
        await _wait_until(lambda: round_runner.calls >= 2, attempts=1_000)
        second_started_at = loop.time()
        assert await service.stop() is True
        return second_started_at - terminal_at

    assert asyncio.run(scenario()) >= 0.045


def test_failed_drain_is_retryable_after_the_active_round_terminates() -> None:
    class IgnoreAbortRound:
        def __init__(self) -> None:
            self.calls = 0
            self.second_started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self, abort_signal: asyncio.Event, /) -> None:
            del abort_signal
            self.calls += 1
            if self.calls == 1:
                return
            self.second_started.set()
            await self.release.wait()

    async def scenario() -> tuple[bool, bool]:
        runner = IgnoreAbortRound()
        service = PeriodicReconciliationService(
            ReconciliationScheduler(runner),
            interval_seconds=0.001,
            startup_timeout_seconds=1,
            drain_timeout_seconds=0.001,
        )
        await service.start()
        await runner.second_started.wait()
        first = await service.stop()
        runner.release.set()
        second = await service.stop()
        return first, second

    assert asyncio.run(scenario()) == (False, True)


def test_periodic_service_cannot_be_restarted() -> None:
    async def scenario() -> None:
        service = _service(_ScriptedRound([None]))
        await service.start()
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            await service.start()
        assert await service.stop() is True

    asyncio.run(scenario())


def test_terminal_scheduler_failure_is_redacted() -> None:
    class FailingScheduler:
        async def tick(self) -> object:
            raise RuntimeError("provider-token=raw-secret")

        async def wait_for_active(self) -> object:
            raise AssertionError("unreachable")

        async def stop_and_drain(self, timeout_seconds: float) -> object:
            del timeout_seconds

            class SafeShutdown:
                persistence_may_close = True

            return SafeShutdown()

    async def scenario() -> tuple[BackgroundHealthState, str, bool]:
        service = PeriodicReconciliationService(
            FailingScheduler(),  # type: ignore[arg-type]
            interval_seconds=60,
            startup_timeout_seconds=1,
            drain_timeout_seconds=1,
        )
        with pytest.raises(InitialReconciliationError, match="initial reconciliation failed"):
            await service.start()
        health = service.background_health()
        return health.state, "terminal failure redacted", await service.stop()

    state, rendered, safe_to_close = asyncio.run(scenario())
    assert state is BackgroundHealthState.TERMINAL_FAILURE
    assert rendered == "terminal failure redacted"
    assert safe_to_close is True


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("broken_sink", [False, True])
def test_outer_loop_cause_does_not_change_terminal_startup_or_stop(
    monkeypatch: pytest.MonkeyPatch, initial: bool, broken_sink: bool
) -> None:
    output = StringIO()

    class Sink(logging.StreamHandler[StringIO]):
        def emit(self, record: logging.LogRecord) -> None:
            if broken_sink:
                raise OSError("private sink failure")
            super().emit(record)

    logger = logging.Logger("periodic-diagnostic")
    logger.addHandler(Sink(output))
    structured = StructuredEventLogger(logger)

    async def scenario() -> None:
        service = _service(
            _ScriptedRound([None]),
            interval_seconds=0.001,
            diagnostics=RuntimeDiagnosticObserver(structured),
        )
        tick = service._scheduler.tick
        calls = 0

        async def failing_tick() -> object:
            nonlocal calls
            calls += 1
            if initial or calls > 1:
                raise KeyError("private scheduler cause")
            return await tick()

        monkeypatch.setattr(service._scheduler, "tick", failing_tick)
        if initial:
            with pytest.raises(InitialReconciliationError, match="initial reconciliation failed"):
                await service.start()
        else:
            await service.start()
            await _wait_until(
                lambda: service.background_health().state is BackgroundHealthState.TERMINAL_FAILURE
            )
        assert service.background_health().state is BackgroundHealthState.TERMINAL_FAILURE
        assert service._startup_complete.is_set()
        assert await service.stop() is True

    asyncio.run(scenario())
    if broken_sink:
        assert structured.failure_count == 1
        assert output.getvalue() == ""
    else:
        row = json.loads(output.getvalue())
        assert (row["stage"], row["exceptionType"], row["level"]) == (
            "reconciliation_loop",
            "KeyError",
            "ERROR",
        )
        assert "private" not in output.getvalue()


def test_round_failure_does_not_become_a_terminal_loop_diagnostic() -> None:
    output = StringIO()
    logger = logging.Logger("round-not-loop")
    logger.addHandler(logging.StreamHandler(output))

    async def scenario() -> None:
        service = _service(
            _ScriptedRound([RuntimeError("private round failure")]),
            diagnostics=RuntimeDiagnosticObserver(StructuredEventLogger(logger)),
        )
        with pytest.raises(InitialReconciliationError):
            await service.start()
        assert service.background_health().state is BackgroundHealthState.STOPPED
        assert await service.stop() is True

    asyncio.run(scenario())
    assert output.getvalue() == ""


@pytest.mark.parametrize("stopping", [False, True])
def test_loop_cancellation_keeps_original_health_without_exception_event(stopping: bool) -> None:
    output = StringIO()
    logger = logging.Logger("loop-cancellation")
    logger.addHandler(logging.StreamHandler(output))

    async def scenario() -> None:
        service = _service(
            _ScriptedRound([None]),
            diagnostics=RuntimeDiagnosticObserver(StructuredEventLogger(logger)),
        )
        await service.start()
        if stopping:
            service._stop_signal.set()
        assert service._loop_task is not None
        service._loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await service._loop_task
        assert service.background_health().state is (
            BackgroundHealthState.STOPPED if stopping else BackgroundHealthState.TERMINAL_FAILURE
        )

    asyncio.run(scenario())
    assert output.getvalue() == ""
