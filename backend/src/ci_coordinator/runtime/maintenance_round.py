"""Compose authoritative reconciliation with bounded maintenance operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ci_coordinator.kernel import MonotonicClock
from ci_coordinator.observability import (
    MaintenanceOperationName,
    MaintenanceOperationResult,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
)

type MaintenanceOperation = Callable[[asyncio.Event], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BoundedMaintenanceOperation:
    name: MaintenanceOperationName
    deadline_seconds: int
    operation: MaintenanceOperation

    def __post_init__(self) -> None:
        if self.name not in {
            "activity_cleanup",
            "ci_economics_collection",
            "ci_economics_expiry",
            "ci_economics_observation_cleanup",
            "ci_economics_tombstone_purge",
            "ci_observation_discovery",
            "ci_observation_gap_cleanup",
            "ci_history_collection",
            "ci_history_delivery",
            "ci_history_detail_cleanup",
        }:
            raise ValueError("maintenance operation name is invalid")
        if type(self.deadline_seconds) is not int or self.deadline_seconds < 1:
            raise ValueError("maintenance operation deadline must be positive")
        if not callable(self.operation):
            raise TypeError("maintenance operation must be callable")


class RuntimeMaintenanceRound:
    """Preserve startup authority while isolating non-authoritative maintenance."""

    def __init__(
        self,
        primary: MaintenanceOperation,
        operations: tuple[BoundedMaintenanceOperation, ...],
        *,
        metrics: RuntimeMetrics,
        clock: MonotonicClock,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        if not callable(primary):
            raise TypeError("primary maintenance operation must be callable")
        if type(operations) is not tuple or any(
            type(operation) is not BoundedMaintenanceOperation for operation in operations
        ):
            raise TypeError("maintenance operations must be an exact tuple")
        names = tuple(operation.name for operation in operations)
        if len(names) != len(set(names)):
            raise ValueError("maintenance operation names must be unique")
        self._primary = primary
        self._operations = operations
        self._metrics = metrics
        self._clock = clock
        self._diagnostics = diagnostics
        self._initial_round = True

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        if self._initial_round:
            await self._run_primary(abort_signal)
            self._initial_round = False
            return
        failure: Exception | None = None
        async with asyncio.TaskGroup() as group:
            if not abort_signal.is_set():
                for operation in self._operations:
                    group.create_task(self._run_bounded(operation, abort_signal))
            try:
                await self._run_primary(abort_signal)
            except Exception as error:
                failure = error
        if failure is not None:
            raise failure

    async def _run_primary(self, abort_signal: asyncio.Event) -> None:
        try:
            await self._primary(abort_signal)
        except Exception as error:
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("reconciliation_round", error)
            raise

    async def _run_bounded(
        self,
        bounded: BoundedMaintenanceOperation,
        abort_signal: asyncio.Event,
    ) -> None:
        started = self._clock.now()
        result: MaintenanceOperationResult
        deadline = asyncio.timeout(bounded.deadline_seconds)
        try:
            async with deadline:
                await bounded.operation(abort_signal)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if isinstance(error, TimeoutError) and deadline.expired():
                result = "timed_out"
            else:
                result = "failed"
                if self._diagnostics is not None:
                    self._diagnostics.unexpected_failure(bounded.name, error)
        else:
            result = "succeeded"
        self._metrics.maintenance_operation(
            bounded.name,
            result,
            duration_seconds=max(0.0, self._clock.now() - started),
        )
