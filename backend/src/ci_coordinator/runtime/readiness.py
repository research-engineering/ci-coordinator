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

    def activate(self) -> None: ...

    def stop(self) -> None: ...

    async def drain(self) -> None: ...


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
        self._names = ("database", *names)
        self._facts: list[bool | None] = []
        self._active = False
        self._stopped = False
        self._resources.bind_readiness(self)

    def activate(self) -> None:
        if self._stopped:
            raise RuntimeError("runtime readiness cannot be restarted")
        self._database_probe.activate()
        self._active = True

    def stop(self) -> None:
        if self._stopped:
            return
        self._active = False
        self._stopped = True
        self._database_probe.stop()
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
        self._inactive_status(publish=True)

    async def drain(self) -> None:
        self.stop()
        task = self._inflight
        cancellation: asyncio.CancelledError | None = None
        if task is not None:
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError as error:
                    cancellation = cancellation or error
            if not task.cancelled():
                task.exception()
        try:
            await self._database_probe.drain()
        except BaseException:
            if cancellation is None:
                raise
            cancellation.add_note("database readiness drain also failed")
        if cancellation is not None:
            raise cancellation

    async def __call__(self) -> ReadinessStatus:
        if not self._active:
            return self._inactive_status()
        if self._active_waiters >= MAXIMUM_CONCURRENT_READINESS_WAITERS:
            return assess_readiness((DependencyReadiness("readiness_admission", False, True),))
        self._active_waiters += 1
        try:
            task = self._inflight
            if task is None or task.done():
                self._facts = [None] * len(self._names)
                deadline = asyncio.get_running_loop().time() + self._timeout_ms / 1_000
                task = asyncio.create_task(self._evaluate(self._facts, deadline))
                self._inflight = task
            try:
                async with asyncio.timeout(self._timeout_ms / 1_000):
                    status = await asyncio.shield(task)
                    return status if self._active else self._inactive_status()
            except TimeoutError:
                if not self._active:
                    return self._inactive_status()
                if task.done():
                    return task.result()
                return self._project(self._facts)
        finally:
            self._active_waiters -= 1

    async def _evaluate(self, facts: list[bool | None], deadline: float) -> ReadinessStatus:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._database_available(facts, deadline))]
            tasks.extend(
                group.create_task(self._probe_available(index, probe, facts, deadline))
                for index, (_name, probe) in enumerate(self._required_probes, start=1)
            )
            await asyncio.gather(*tasks)
        if not self._active or self._facts is not facts:
            return self._inactive_status()
        return self._project(facts)

    def _project(self, facts: list[bool | None]) -> ReadinessStatus:
        if not self._active or facts is not self._facts:
            return self._inactive_status()
        background = self._resources.background_health()
        if self._metrics is not None:
            self._metrics.background_health(background)
        dependencies = (
            DependencyReadiness(
                "runtime_resources",
                self._resources.lifecycle_ready,
                True,
            ),
            DependencyReadiness("background_reconciliation", background.ready, True),
            *(
                DependencyReadiness(name, available is True, True)
                for name, available in zip(self._names, facts, strict=True)
            ),
        )
        status = assess_readiness(dependencies)
        self._observe(
            status,
            dependencies,
            unresolved=tuple(
                name for name, value in zip(self._names, facts, strict=True) if value is None
            ),
        )
        return status

    async def _probe_available(
        self,
        index: int,
        probe: RuntimeAvailabilityProbe,
        facts: list[bool | None],
        deadline: float,
    ) -> None:
        timeout = asyncio.timeout_at(deadline)
        try:
            async with timeout:
                available = await probe.probe() is True
        except Exception as error:
            if isinstance(error, TimeoutError) and timeout.expired():
                return
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("readiness_dependency", error)
            available = False
        if self._active and self._facts is facts and asyncio.get_running_loop().time() < deadline:
            facts[index] = available

    async def _database_available(self, facts: list[bool | None], deadline: float) -> None:
        timeout = asyncio.timeout_at(deadline)
        try:
            async with timeout:
                database = await self._database_probe.check()
                available = database.ready
        except Exception as error:
            if isinstance(error, TimeoutError) and timeout.expired():
                return
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("readiness_database", error)
            available = False
        if self._active and self._facts is facts and asyncio.get_running_loop().time() < deadline:
            facts[0] = available

    def _inactive_status(self, *, publish: bool = False) -> ReadinessStatus:
        background = self._resources.background_health()
        if publish and self._metrics is not None:
            self._metrics.background_health(background)
        dependencies = (
            DependencyReadiness(
                "runtime_resources",
                False,
                True,
            ),
            DependencyReadiness(
                "background_reconciliation",
                background.ready,
                True,
            ),
        )
        status = assess_readiness(dependencies)
        if publish:
            self._observe(
                status,
                dependencies,
                unresolved=("database", *(name for name, _ in self._required_probes)),
            )
        return status

    def _observe(
        self,
        status: ReadinessStatus,
        dependencies: tuple[DependencyReadiness, ...],
        *,
        unresolved: tuple[str, ...] = (),
    ) -> None:
        if self._metrics is None:
            return
        for name in unresolved:
            self._metrics.dependency_unresolved(name)
        for dependency in dependencies:
            if dependency.available:
                self._metrics.dependency_ready(dependency.name)
        self._metrics.readiness(
            ready=status.ready,
            unavailable_dependencies=tuple(
                name for name in status.unavailable_dependencies if name not in unresolved
            ),
        )
