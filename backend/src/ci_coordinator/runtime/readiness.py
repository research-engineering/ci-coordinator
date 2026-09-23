"""Runtime readiness projected from concrete dependency checks."""

from __future__ import annotations

import asyncio
from typing import Protocol

from ci_coordinator.observability import (
    DependencyReadiness,
    ReadinessStatus,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    assess_readiness,
)
from ci_coordinator.persistence import DatabaseReadiness
from ci_coordinator.runtime.resources import RuntimeResources

MAXIMUM_CONCURRENT_READINESS_WAITERS = 8


class RuntimeAvailabilityProbe(Protocol):
    async def probe(self) -> bool: ...


class DatabaseStatusProbe(Protocol):
    async def check(self) -> DatabaseReadiness: ...


class RuntimeReadiness:
    def __init__(
        self,
        *,
        resources: RuntimeResources,
        database_probe: DatabaseStatusProbe,
        timeout_ms: int,
        required_probes: tuple[tuple[str, RuntimeAvailabilityProbe], ...] = (),
        metrics: RuntimeMetrics | None = None,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise ValueError("readiness timeout must be positive milliseconds")
        self._resources = resources
        self._timeout_ms = timeout_ms
        self._database_probe = database_probe
        names = tuple(name for name, _probe in required_probes)
        if any(type(name) is not str or not name for name in names):
            raise ValueError("readiness dependency identifiers must be non-empty text")
        if len(names) != len(set(names)):
            raise ValueError("readiness dependency identifiers must be unique")
        self._required_probes = required_probes
        self._metrics = metrics
        self._diagnostics = diagnostics
        self._inflight: asyncio.Task[ReadinessStatus] | None = None
        self._active_waiters = 0

    async def __call__(self) -> ReadinessStatus:
        if self._active_waiters >= MAXIMUM_CONCURRENT_READINESS_WAITERS:
            return self._unavailable_status("readiness_admission")
        self._active_waiters += 1
        try:
            task = self._inflight
            if task is None or task.done():
                task = asyncio.create_task(self._evaluate())
                self._inflight = task
            try:
                async with asyncio.timeout(self._timeout_ms / 1_000):
                    return await asyncio.shield(task)
            except TimeoutError:
                return self._async_dependencies_unavailable()
        finally:
            self._active_waiters -= 1

    async def _evaluate(self) -> ReadinessStatus:
        availability = await asyncio.gather(
            self._database_available(),
            *(self._probe_available(name, probe) for name, probe in self._required_probes),
        )
        database_available = availability[0]
        background = self._resources.background_health()
        if self._metrics is not None:
            self._metrics.background_health(background)
        probed_dependencies = tuple(
            DependencyReadiness(name, available, True)
            for (name, _probe), available in zip(
                self._required_probes,
                availability[1:],
                strict=True,
            )
        )
        dependencies = (
            DependencyReadiness(
                "runtime_resources",
                self._resources.lifecycle_ready,
                True,
            ),
            DependencyReadiness("database", database_available, True),
            DependencyReadiness("background_reconciliation", background.ready, True),
            *probed_dependencies,
        )
        status = assess_readiness(dependencies)
        self._observe(status, dependencies)
        return status

    async def _probe_available(
        self,
        _name: str,
        probe: RuntimeAvailabilityProbe,
    ) -> bool:
        try:
            async with asyncio.timeout(self._timeout_ms / 1_000):
                return await probe.probe() is True
        except TimeoutError:
            return False
        except Exception as error:
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("readiness_dependency", error)
            return False

    async def _database_available(self) -> bool:
        try:
            async with asyncio.timeout(self._timeout_ms / 1_000):
                database = await self._database_probe.check()
        except TimeoutError:
            return False
        except Exception as error:
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("readiness_database", error)
            return False
        return database.ready

    def _async_dependencies_unavailable(self) -> ReadinessStatus:
        background = self._resources.background_health()
        if self._metrics is not None:
            self._metrics.background_health(background)
        dependencies = (
            DependencyReadiness(
                "runtime_resources",
                self._resources.lifecycle_ready,
                True,
            ),
            DependencyReadiness("database", False, True),
            DependencyReadiness(
                "background_reconciliation",
                background.ready,
                True,
            ),
            *(DependencyReadiness(name, False, True) for name, _probe in self._required_probes),
        )
        status = assess_readiness(dependencies)
        self._observe(status, dependencies)
        return status

    def _unavailable_status(self, dependency_name: str) -> ReadinessStatus:
        dependencies = (DependencyReadiness(dependency_name, False, True),)
        status = assess_readiness(dependencies)
        self._observe(status, dependencies)
        return status

    def _observe(
        self,
        status: ReadinessStatus,
        dependencies: tuple[DependencyReadiness, ...],
    ) -> None:
        if self._metrics is None:
            return
        for dependency in dependencies:
            if dependency.available:
                self._metrics.dependency_ready(dependency.name)
        self._metrics.readiness(
            ready=status.ready,
            unavailable_dependencies=status.unavailable_dependencies,
        )
