import asyncio
from collections.abc import Awaitable, Callable
from math import isfinite
from typing import Final

from ci_coordinator.app.ci_history_collection import (
    HISTORY_WORK_LANES,
    HistoryItemOutcome,
    HistoryWorkLane,
)
from ci_coordinator.observability import RuntimeDiagnosticObserver, RuntimeMetrics

HISTORY_ITEM_TIMEOUT_SECONDS: Final = 45
type CollectHistoryItem = Callable[[HistoryWorkLane, asyncio.Event], Awaitable[HistoryItemOutcome]]


class HistoryCollectionWorker:
    def __init__(
        self,
        collect: CollectHistoryItem,
        *,
        idle_seconds: float,
        drain_seconds: float,
        metrics: RuntimeMetrics,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        if not callable(collect):
            raise TypeError("history worker requires a collection operation")
        for value in (idle_seconds, drain_seconds):
            if type(value) not in {int, float} or not isfinite(value) or value <= 0:
                raise ValueError("history worker waits must be finite and positive")
        self._collect = collect
        self._idle_seconds = float(idle_seconds)
        self._drain_seconds = float(drain_seconds)
        self._metrics = metrics
        self._diagnostics = diagnostics
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None or self._stop.is_set():
            raise RuntimeError("history worker cannot be restarted")
        for lane in HISTORY_WORK_LANES:
            self._metrics.ci_history_worker_running(lane, False)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> bool:
        first_stop = not self._stop.is_set()
        self._stop.set()
        task = self._task
        if task is None:
            return True
        if first_stop and not task.done():
            task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=self._drain_seconds)
        if task not in done:
            return False
        if not task.cancelled():
            task.result()
        return True

    async def _run(self) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                for lane in HISTORY_WORK_LANES:
                    group.create_task(self._run_lane(lane))
        except Exception as error:
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("ci_history_collection", error)
        finally:
            for lane in HISTORY_WORK_LANES:
                self._metrics.ci_history_worker_running(lane, False)

    async def _run_lane(self, lane: HistoryWorkLane) -> None:
        self._metrics.ci_history_worker_running(lane, True)
        try:
            while not self._stop.is_set():
                deadline = asyncio.timeout(HISTORY_ITEM_TIMEOUT_SECONDS)
                try:
                    async with deadline:
                        outcome = await self._collect(lane, self._stop)
                except TimeoutError:
                    if not deadline.expired():
                        raise
                    outcome = "aborted"
                if outcome in {"applied", "recovered"}:
                    await asyncio.sleep(0)
                elif not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self._idle_seconds)
                    except TimeoutError:
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("ci_history_collection", error)
        finally:
            self._metrics.ci_history_worker_running(lane, False)
