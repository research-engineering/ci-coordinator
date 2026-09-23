import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest
from fastapi import FastAPI
from runtime.test_runtime_resources import _resources

from ci_coordinator.observability import BackgroundHealth, BackgroundHealthState
from ci_coordinator.runtime.background_services import RuntimeBackgroundGroup


@dataclass
class _Service:
    name: str
    events: list[str]
    start_gate: asyncio.Event | None = None
    stop_gate: asyncio.Event | None = None
    start_error: Exception | None = None
    stop_error: BaseException | None = None
    stop_result: bool = True
    health: BackgroundHealthState = BackgroundHealthState.HEALTHY
    starting: asyncio.Event = field(default_factory=asyncio.Event)
    stopping: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        self.events.append(f"start-{self.name}")
        self.starting.set()
        if self.start_gate is not None:
            await self.start_gate.wait()
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> bool:
        self.events.append(f"stop-{self.name}")
        self.stopping.set()
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop_result

    def background_health(self) -> BackgroundHealth:
        return BackgroundHealth(self.health)


async def test_primary_admission_precedes_secondary_start_and_drain_is_concurrent() -> None:
    events: list[str] = []
    ready, release = asyncio.Event(), asyncio.Event()
    primary = _Service("primary", events, start_gate=ready, stop_gate=release)
    auxiliary = _Service("auxiliary", events, stop_gate=release)
    group = RuntimeBackgroundGroup(primary=primary, additional=(auxiliary,))
    async with asyncio.timeout(3):
        start = asyncio.create_task(group.start())
        try:
            await primary.starting.wait()
            await asyncio.sleep(0)
            assert events == ["start-primary"]
            with pytest.raises(RuntimeError, match="settle"):
                await group.stop()
            ready.set()
            await start
            assert events == ["start-primary", "start-auxiliary"]
            stop = asyncio.create_task(group.stop())
            try:
                await primary.stopping.wait()
                await auxiliary.stopping.wait()
                assert not stop.done()
            finally:
                release.set()
                assert await stop
        finally:
            ready.set()
            release.set()
            await start


@pytest.mark.parametrize("failed", ["primary", "auxiliary"])
async def test_failed_start_retains_every_attempted_owner_and_no_later_owner(failed: str) -> None:
    events: list[str] = []
    primary, auxiliary, later = (
        _Service(name, events) for name in ("primary", "auxiliary", "later")
    )
    (primary if failed == "primary" else auxiliary).start_error = RuntimeError("startup failed")
    group = RuntimeBackgroundGroup(primary=primary, additional=(auxiliary, later))
    with pytest.raises(RuntimeError, match="startup failed"):
        await group.start()
    assert await group.stop()
    attempted = ["primary"] if failed == "primary" else ["primary", "auxiliary"]
    assert events[: len(attempted)] == [f"start-{name}" for name in attempted]
    assert sorted(events[len(attempted) :]) == sorted(f"stop-{name}" for name in attempted)
    with pytest.raises(RuntimeError, match="restarted"):
        await group.start()


@pytest.mark.parametrize("failure", [False, 1, RuntimeError("private"), asyncio.CancelledError()])
async def test_drain_requires_exact_success_from_every_attempted_owner(failure: object) -> None:
    events: list[str] = []
    primary, auxiliary = _Service("primary", events), _Service("auxiliary", events)
    if isinstance(failure, BaseException):
        auxiliary.stop_error = failure
    else:
        auxiliary.stop_result = cast(bool, failure)
    group = RuntimeBackgroundGroup(primary=primary, additional=(auxiliary,))
    await group.start()
    assert await group.stop() is False
    auxiliary.stop_error = None
    auxiliary.stop_result = True
    assert await group.stop() is True
    assert events.count("start-primary") == events.count("start-auxiliary") == 1
    assert events.count("stop-primary") == events.count("stop-auxiliary") == 2


async def test_auxiliary_drain_blocks_disposal_without_changing_primary_health() -> None:
    events: list[str] = []
    primary = _Service("primary", events)
    auxiliary = _Service("auxiliary", events, stop_result=False)
    group = RuntimeBackgroundGroup(primary=primary, additional=(auxiliary,))
    resources = _resources(events, group)
    with pytest.raises(RuntimeError, match="did not drain"):
        async with resources.lifespan(FastAPI()):
            auxiliary.health = BackgroundHealthState.TERMINAL_FAILURE
            assert resources.background_health().ready
    assert not any(name in events for name in ("github", "jwks", "engine"))
    auxiliary.stop_result = True
    await resources.aclose()
    assert events[-3:] == ["github", "jwks", "engine"]


async def test_cancelled_resource_startup_starts_no_secondary_before_external_cleanup() -> None:
    events: list[str] = []
    primary = _Service("primary", events, start_gate=asyncio.Event())
    auxiliary = _Service("auxiliary", events)
    resources = _resources(events, RuntimeBackgroundGroup(primary=primary, additional=(auxiliary,)))

    async def serve() -> None:
        async with resources.lifespan(FastAPI()):
            raise AssertionError("startup was not admitted")

    async with asyncio.timeout(3):
        serving = asyncio.create_task(serve())
        await primary.starting.wait()
        serving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serving
    assert events == ["start-primary", "stop-primary", "github", "jwks", "engine"]


def test_duplicate_owners_are_rejected_before_startup() -> None:
    events: list[str] = []
    primary = _Service("primary", events)
    with pytest.raises(ValueError, match="distinct"):
        RuntimeBackgroundGroup(primary=primary, additional=(primary,))
    assert events == []
