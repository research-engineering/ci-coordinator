from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from ci_coordinator.app.planning_preparation import PlanningPreparationOutcome, preparation_epoch
from ci_coordinator.github_ingestion.seeds import DynamicCiSeed
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.repo_context.diff_model import RepositoryEpoch

_QUEUE_LIMIT = 32
_ATTEMPT_SECONDS = 5.0


class PlanningPreparationQueue:
    def __init__(
        self,
        prepare_context: Callable[[RepositoryEpoch], Awaitable[PlanningPreparationOutcome]],
        close_cache: Callable[[], None],
        metrics: RuntimeMetrics,
    ) -> None:
        self._prepare_context = prepare_context
        self._close_cache = close_cache
        self._metrics = metrics
        self._queue: asyncio.Queue[RepositoryEpoch] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        self._pending: set[RepositoryEpoch] = set()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def prepare(self) -> None:
        if self._closed or self._task is not None:
            raise RuntimeError("planning preparation cannot be started twice")
        self._task = asyncio.create_task(self._run(), name="planning-context-preparation")

    def offer(self, seed: DynamicCiSeed) -> bool:
        if self._closed or self._task is None or self._task.done():
            self._metrics.planning_preparation("unavailable")
            return False
        epoch = preparation_epoch(seed)
        if epoch is None:
            self._metrics.planning_preparation("ineligible")
            return False
        if epoch in self._pending:
            self._metrics.planning_preparation("coalesced")
            return True
        try:
            self._queue.put_nowait(epoch)
        except asyncio.QueueFull:
            self._metrics.planning_preparation("saturated")
            return False
        self._pending.add(epoch)
        self._metrics.planning_preparation("queued")
        return True

    async def _run(self) -> None:
        while True:
            epoch = await self._queue.get()
            try:
                async with asyncio.timeout(_ATTEMPT_SECONDS):
                    outcome = await self._prepare_context(epoch)
                self._metrics.planning_preparation(outcome)
            except TimeoutError:
                self._metrics.planning_preparation("timed_out")
            except asyncio.CancelledError:
                self._metrics.planning_preparation("cancelled")
                raise
            except Exception:
                self._metrics.planning_preparation("failed")
            finally:
                self._pending.discard(epoch)
                self._queue.task_done()

    async def aclose(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._pending.clear()
        self._close_cache()
