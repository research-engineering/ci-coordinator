import asyncio
from typing import cast
from unittest.mock import Mock

import pytest
from prometheus_support import prometheus_samples

from ci_coordinator.app.ci_history_collection import (
    HISTORY_WORK_LANES,
    HistoryItemOutcome,
    HistoryWorkLane,
)
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.runtime import history_collection_worker
from ci_coordinator.runtime.history_collection_worker import (
    CollectHistoryItem,
    HistoryCollectionWorker,
)


def _worker(
    collect: CollectHistoryItem,
    *,
    idle: float = 60,
    drain: float = 1,
    metrics: RuntimeMetrics | None = None,
) -> HistoryCollectionWorker:
    return HistoryCollectionWorker(
        collect,
        idle_seconds=idle,
        drain_seconds=drain,
        metrics=metrics if metrics is not None else RuntimeMetrics(),
    )


def _running(metrics: RuntimeMetrics, lane: HistoryWorkLane) -> float:
    return prometheus_samples(metrics).get(
        ("ci_coordinator_ci_history_worker_running", (("lane", lane),)), 0
    )


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf"), "1"])
@pytest.mark.parametrize("field", ["idle", "drain"])
def test_waits_are_admitted_before_tasks(value: object, field: str) -> None:
    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        raise AssertionError("invalid configuration reached collection")

    with pytest.raises(ValueError):
        _worker(
            collect,
            idle=cast(float, value) if field == "idle" else 60,
            drain=cast(float, value) if field == "drain" else 1,
        )


async def test_progress_continues_beyond_the_old_batch_and_yields_to_other_lanes() -> None:
    calls = dict.fromkeys(HISTORY_WORK_LANES, 0)
    observed: list[int] = []
    completed = asyncio.Event()

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        calls[lane] += 1
        if lane == "recent" and calls[lane] == 1:
            observed.append(calls["backfill"])
        if lane == "backfill":
            if calls[lane] == 25:
                completed.set()
            if calls[lane] < 25:
                return "applied"
        return "none_due"

    worker = _worker(collect)
    async with asyncio.timeout(3):
        await worker.start()
        try:
            await completed.wait()
            assert calls["backfill"] == 25
            assert observed and 0 < observed[0] < 25
        finally:
            assert await worker.stop()


async def test_each_item_receives_its_own_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    original_time = loop.time
    offset = 0.0
    started = 0
    completed: list[int] = []
    observed = asyncio.Event()

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        nonlocal offset, started
        if lane != "backfill":
            return "none_due"
        started += 1
        try:
            offset += 30
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            completed.append(started)
        except asyncio.CancelledError:
            observed.set()
            raise
        if started == 2:
            observed.set()
            return "none_due"
        return "applied"

    worker = _worker(collect)
    with monkeypatch.context() as clock:
        clock.setattr(loop, "time", lambda: original_time() + offset)
        async with asyncio.timeout(180):
            await worker.start()
            try:
                await observed.wait()
                assert completed == [1, 2]
            finally:
                assert await worker.stop()


@pytest.mark.parametrize(
    "outcome", ["none_due", "capacity_reached", "claim_lost", "store_unavailable"]
)
async def test_nonprogress_waits_without_busy_polling_and_stop_interrupts_idle(
    outcome: HistoryItemOutcome,
) -> None:
    calls = dict.fromkeys(HISTORY_WORK_LANES, 0)
    entered = asyncio.Event()
    metrics = RuntimeMetrics()

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        calls[lane] += 1
        if all(calls.values()):
            entered.set()
        await asyncio.sleep(0)
        return outcome

    worker = _worker(collect, metrics=metrics)
    async with asyncio.timeout(3):
        await worker.start()
        try:
            await entered.wait()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert set(calls.values()) == {1}
            assert all(_running(metrics, lane) == 1 for lane in HISTORY_WORK_LANES)
        finally:
            assert await worker.stop()
    assert all(_running(metrics, lane) == 0 for lane in HISTORY_WORK_LANES)


async def test_one_blocked_lane_preserves_independent_progress_and_single_flight() -> None:
    entered, release, progress = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = dict.fromkeys(HISTORY_WORK_LANES, 0)
    peak = dict(active)
    recent = 0

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        nonlocal recent
        active[lane] += 1
        peak[lane] = max(peak[lane], active[lane])
        try:
            if lane == "backfill":
                entered.set()
                await release.wait()
            if lane == "recent":
                recent += 1
                if recent == 20:
                    progress.set()
                if recent < 20:
                    return "recovered"
            return "none_due"
        finally:
            active[lane] -= 1

    worker = _worker(collect)
    async with asyncio.timeout(3):
        await worker.start()
        try:
            await entered.wait()
            await progress.wait()
            assert active["backfill"] == 1 and recent == 20
            assert set(peak.values()) == {1}
        finally:
            release.set()
            assert await worker.stop()


async def test_item_timeout_retains_a_recovery_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history_collection_worker, "HISTORY_ITEM_TIMEOUT_SECONDS", 0.01)
    recovered = asyncio.Event()
    attempts = 0

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        nonlocal attempts
        if lane == "backfill":
            attempts += 1
            if attempts == 1:
                await asyncio.Event().wait()
            recovered.set()
        return "none_due"

    worker = _worker(collect, idle=0.01)
    async with asyncio.timeout(3):
        await worker.start()
        try:
            await recovered.wait()
            assert attempts == 2
        finally:
            assert await worker.stop()


@pytest.mark.parametrize(
    "error", [TimeoutError("inner"), RuntimeError("private"), asyncio.CancelledError()]
)
async def test_unexpected_lane_exit_is_visible_without_cancelling_other_lanes(
    error: BaseException,
) -> None:
    peers = asyncio.Event()
    metrics = RuntimeMetrics()
    seen: set[HistoryWorkLane] = set()

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        if lane == "backfill":
            raise error
        seen.add(lane)
        if len(seen) == 3:
            peers.set()
        return "none_due"

    worker = _worker(collect, metrics=metrics)
    async with asyncio.timeout(3):
        await worker.start()
        try:
            await peers.wait()
            assert _running(metrics, "backfill") == 0
            assert all(_running(metrics, lane) == 1 for lane in seen)
            assert worker._task is not None and not worker._task.done()
        finally:
            assert await worker.stop()


async def test_failed_drain_preserves_the_same_task_until_cleanup_settles() -> None:
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = 0

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        nonlocal cancellations
        if lane == "backfill":
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations += 1
                cleaning.set()
                await release.wait()
                raise
        return "none_due"

    worker = _worker(collect, drain=0.01)
    async with asyncio.timeout(3):
        await worker.start()
        await entered.wait()
        task = worker._task
        try:
            assert await worker.stop() is False
            await cleaning.wait()
            assert await worker.stop() is False
            assert worker._task is task and task is not None and not task.done()
            assert cancellations == 1
        finally:
            release.set()
            assert await worker.stop()
        with pytest.raises(RuntimeError, match="restarted"):
            await worker.start()


async def test_stop_before_start_is_terminal_and_creates_no_task() -> None:
    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        raise AssertionError("stopped worker executed an item")

    worker = _worker(collect)
    assert await worker.stop()
    with pytest.raises(RuntimeError, match="restarted"):
        await worker.start()
    assert worker._task is None


@pytest.mark.parametrize(
    "outcome",
    [
        "none_due",
        "capacity_reached",
        "claim_lost",
        "store_unavailable",
        "aborted",
        "unexpected_error",
    ],
)
async def test_configured_idle_precedes_retry_and_progress(
    outcome: HistoryItemOutcome, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    original_time, original_wait = loop.time, asyncio.wait_for
    offset = 0.0
    calls = 0
    entered, retried, release, progressed = (asyncio.Event() for _ in range(4))

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        nonlocal calls
        if lane != "backfill":
            return "none_due"
        calls += 1
        if calls == 1:
            entered.set()
            return outcome
        if calls == 2:
            retried.set()
            await release.wait()
            return "applied"
        progressed.set()
        return "none_due"

    waits = Mock(wraps=original_wait)
    worker = _worker(collect)
    with monkeypatch.context() as clock:
        clock.setattr(loop, "time", lambda: original_time() + offset)
        # noinspection PyUnresolvedReferences
        clock.setattr(asyncio, "wait_for", waits)
        async with asyncio.timeout(180):
            await worker.start()
            try:
                await entered.wait()
                assert waits.call_count == len(HISTORY_WORK_LANES)
                assert all(call.kwargs["timeout"] == 60 for call in waits.call_args_list)
                offset += 30
                for _ in range(6):
                    await asyncio.sleep(0)
                assert calls == 1 and not retried.is_set()
                offset += 31
                await original_wait(retried.wait(), timeout=1)
                assert calls == 2
                release.set()
                await original_wait(progressed.wait(), timeout=1)
                assert calls == 3
            finally:
                release.set()
                assert await worker.stop()


@pytest.mark.parametrize("outcome", ["applied", "recovered"])
async def test_normal_completion_after_stop_never_starts_another_item(
    outcome: HistoryItemOutcome,
) -> None:
    entered, cleaning, release = (asyncio.Event() for _ in range(3))
    calls = 0

    async def collect(lane: HistoryWorkLane, abort: asyncio.Event) -> HistoryItemOutcome:
        nonlocal calls
        if lane != "backfill":
            return "none_due"
        calls += 1
        if calls > 1:
            raise AssertionError("stopped worker began a replacement item")
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert abort.is_set()
            cleaning.set()
            await release.wait()
        return outcome

    worker = _worker(collect, drain=0.01)
    async with asyncio.timeout(3):
        await worker.start()
        await entered.wait()
        original = worker._task
        try:
            assert await worker.stop() is False
            await cleaning.wait()
            assert await worker.stop() is False
            assert worker._task is original and calls == 1
        finally:
            release.set()
            assert await worker.stop()
        assert calls == 1 and worker._task is original
        assert original is not None and original.done()
