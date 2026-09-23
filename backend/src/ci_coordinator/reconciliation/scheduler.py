"""Single-flight reconciliation scheduling with fail-closed shutdown."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ReconciliationRound(Protocol):
    async def __call__(self, abort_signal: asyncio.Event, /) -> None: ...


class TickResult(StrEnum):
    STARTED = "started"
    STARTED_AFTER_FAILURE = "started_after_failure"
    ALREADY_RUNNING = "already_running"
    STOPPED = "stopped"


class DrainResult(StrEnum):
    DRAINED = "drained"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


class RoundCompletion(StrEnum):
    ABSENT = "absent"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SchedulerShutdown:
    result: DrainResult

    @property
    def persistence_may_close(self) -> bool:
        return self.result is not DrainResult.TIMED_OUT


class ReconciliationScheduler:
    """Starts at most one round and never force-closes its external resources."""

    def __init__(self, round_runner: ReconciliationRound) -> None:
        self._round_runner = round_runner
        self._stopped = False
        self._active_task: asyncio.Task[None] | None = None
        self._abort_signal: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    async def tick(self) -> TickResult:
        async with self._lock:
            if self._stopped:
                return TickResult.STOPPED
            if self._active_task is not None and not self._active_task.done():
                return TickResult.ALREADY_RUNNING
            previous_failed = self._consume_completed_task()
            abort_signal = asyncio.Event()
            self._abort_signal = abort_signal
            self._active_task = asyncio.create_task(self._round_runner(abort_signal))
            return TickResult.STARTED_AFTER_FAILURE if previous_failed else TickResult.STARTED

    def _consume_completed_task(self) -> bool:
        task = self._active_task
        if task is None:
            return False
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            return True
        return False

    async def wait_for_active(self) -> RoundCompletion:
        """Wait for the accepted round without transferring caller cancellation."""
        async with self._lock:
            task = self._active_task
        if task is None:
            return RoundCompletion.ABSENT
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return RoundCompletion.FAILED
            raise
        except Exception:
            return RoundCompletion.FAILED
        return RoundCompletion.SUCCEEDED

    async def stop_and_drain(self, timeout_seconds: float) -> SchedulerShutdown:
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("scheduler drain timeout must be positive")
        async with self._lock:
            self._stopped = True
            task = self._active_task
            if self._abort_signal is not None:
                self._abort_signal.set()
        if task is None:
            return SchedulerShutdown(DrainResult.DRAINED)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except TimeoutError:
            return SchedulerShutdown(DrainResult.TIMED_OUT)
        except asyncio.CancelledError:
            if task.cancelled():
                return SchedulerShutdown(DrainResult.FAILED)
            raise
        except Exception:
            return SchedulerShutdown(DrainResult.FAILED)
        return SchedulerShutdown(DrainResult.DRAINED)
