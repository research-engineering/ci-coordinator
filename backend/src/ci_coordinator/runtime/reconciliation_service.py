"""Periodic process-lifetime ownership for reconciliation rounds."""

from __future__ import annotations

import asyncio

from ci_coordinator.observability import (
    BackgroundHealth,
    BackgroundHealthState,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
)
from ci_coordinator.reconciliation import ReconciliationScheduler, RoundCompletion
from ci_coordinator.reconciliation.scheduler import TickResult


class InitialReconciliationError(RuntimeError):
    """The runtime did not establish its initial reconciliation invariant."""


class PeriodicReconciliationService:
    """Tick one single-flight scheduler until process shutdown."""

    def __init__(
        self,
        scheduler: ReconciliationScheduler,
        *,
        interval_seconds: float,
        startup_timeout_seconds: float,
        drain_timeout_seconds: float,
        metrics: RuntimeMetrics | None = None,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        for value, name in (
            (interval_seconds, "reconciliation interval"),
            (startup_timeout_seconds, "reconciliation startup timeout"),
            (drain_timeout_seconds, "reconciliation drain timeout"),
        ):
            if type(value) not in {int, float} or value <= 0:
                raise ValueError(f"{name} must be positive")
        self._scheduler = scheduler
        self._interval_seconds = float(interval_seconds)
        self._startup_timeout_seconds = float(startup_timeout_seconds)
        self._drain_timeout_seconds = float(drain_timeout_seconds)
        self._metrics = metrics
        self._diagnostics = diagnostics
        self._stop_signal = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._terminal_failure = False
        self._startup_complete = asyncio.Event()
        self._initial_completion = RoundCompletion.ABSENT
        self._round_healthy = False

    async def start(self) -> None:
        if self._loop_task is not None:
            raise RuntimeError("periodic reconciliation service cannot be restarted")
        self._loop_task = asyncio.create_task(self._run())
        try:
            async with asyncio.timeout(self._startup_timeout_seconds):
                await self._startup_complete.wait()
        except TimeoutError:
            self._round_healthy = False
            raise InitialReconciliationError("initial reconciliation timed out") from None
        if self._initial_completion is not RoundCompletion.SUCCEEDED:
            raise InitialReconciliationError("initial reconciliation failed")

    async def stop(self) -> bool:
        self._stop_signal.set()
        shutdown = await self._scheduler.stop_and_drain(self._drain_timeout_seconds)
        if not shutdown.persistence_may_close:
            return False
        if self._loop_task is not None:
            await self._loop_task
        return True

    def background_health(self) -> BackgroundHealth:
        if self._terminal_failure:
            return BackgroundHealth(BackgroundHealthState.TERMINAL_FAILURE)
        if self._loop_task is None:
            return BackgroundHealth(BackgroundHealthState.STARTING)
        if self._stop_signal.is_set() or self._loop_task.done():
            return BackgroundHealth(BackgroundHealthState.STOPPED)
        state = (
            BackgroundHealthState.HEALTHY if self._round_healthy else BackgroundHealthState.DEGRADED
        )
        return BackgroundHealth(state)

    async def _run(self) -> None:
        first_round = True
        try:
            while not self._stop_signal.is_set():
                result = await self._scheduler.tick()
                if result is TickResult.STOPPED:
                    break
                completion = await self._scheduler.wait_for_active()
                if first_round and self._stop_signal.is_set():
                    self._round_healthy = False
                    self._startup_complete.set()
                    return
                self._round_healthy = completion is RoundCompletion.SUCCEEDED
                if self._metrics is not None:
                    self._metrics.reconciliation_round(
                        "succeeded" if self._round_healthy else "failed"
                    )
                if first_round:
                    self._initial_completion = completion
                    self._startup_complete.set()
                    if not self._round_healthy:
                        return
                    first_round = False
                if self._stop_signal.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        self._stop_signal.wait(),
                        timeout=self._interval_seconds,
                    )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            self._terminal_failure = not self._stop_signal.is_set()
            self._startup_complete.set()
            raise
        except Exception as error:
            self._terminal_failure = True
            self._round_healthy = False
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("reconciliation_loop", error)
        finally:
            self._startup_complete.set()
