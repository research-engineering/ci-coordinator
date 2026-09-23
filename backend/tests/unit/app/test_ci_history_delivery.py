import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

import pytest
from prometheus_support import prometheus_samples

from ci_coordinator.app import ci_history_delivery as delivery
from ci_coordinator.ci_economics.history_ports import HistoryDeliveryTransfer
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeMetrics


def _scopes(count: int) -> tuple[RepositoryScope, ...]:
    return tuple(RepositoryScope(101, index) for index in range(1, count + 1))


@dataclass
class _Store:
    scopes: tuple[RepositoryScope, ...] = _scopes(20)
    hook: Callable[[RepositoryScope], Awaitable[None]] | None = None
    list_hook: Callable[[], Awaitable[None]] | None = None
    calls: list[RepositoryScope] = field(default_factory=list)
    listings: int = 0
    expired_sample: int | None = 3

    async def list_delivery_scopes(self) -> tuple[RepositoryScope, ...]:
        self.listings += 1
        if self.list_hook is not None:
            await self.list_hook()
        return self.scopes

    async def transfer_deliveries(self, scope: RepositoryScope) -> HistoryDeliveryTransfer:
        self.calls.append(scope)
        if self.hook is not None:
            await self.hook(scope)
        return HistoryDeliveryTransfer("applied", 2, 1, 60.0, self.expired_sample)


def test_scope_rotation_is_bounded_and_survives_population_changes() -> None:
    async def scenario() -> None:
        store = _Store(_scopes(32))
        metrics = RuntimeMetrics()
        service = delivery.CiHistoryDeliveryService(store, metrics)
        abort = asyncio.Event()
        assert await service.run(abort) == ("applied",) * 16
        assert store.calls == list(_scopes(16))
        assert await service.run(abort) == ("applied",) * 16
        assert store.calls == list(_scopes(32))
        store.scopes = (*_scopes(3), RepositoryScope(101, 33))
        assert await service.run(abort) == ("applied",) * 4
        assert store.calls[-4:] == [RepositoryScope(101, 33), *_scopes(3)]
        samples = prometheus_samples(metrics)
        assert (
            samples[
                ("ci_coordinator_ci_history_delivery_sources_total", (("stage", "candidates"),))
            ]
            == 72
        )
        assert (
            samples[
                ("ci_coordinator_ci_history_delivery_sources_total", (("stage", "transferred"),))
            ]
            == 36
        )
        assert samples[("ci_coordinator_ci_history_pending_age_seconds_count", ())] == 36
        assert samples[("ci_coordinator_ci_history_pending_age_seconds_sum", ())] == 2160
        assert samples[("ci_coordinator_ci_history_expired_pending_sample_count", ())] == 36
        assert samples[("ci_coordinator_ci_history_expired_pending_sample_sum", ())] == 108

    asyncio.run(scenario())


@pytest.mark.parametrize("sample", [None, 0, 100, True, -1, 101, 1.5, "1"])
def test_expiry_sample_has_distinct_unobserved_empty_and_invalid_states(sample: object) -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        store = _Store(scopes=_scopes(1), expired_sample=cast(int | None, sample))
        assert await delivery.CiHistoryDeliveryService(store, metrics).run(asyncio.Event()) == (
            "applied",
        )
        samples = prometheus_samples(metrics)
        valid = type(sample) is int and 0 <= sample <= 100
        assert samples[("ci_coordinator_ci_history_expired_pending_sample_count", ())] == int(valid)
        assert samples[("ci_coordinator_ci_history_expired_pending_sample_sum", ())] == (
            sample if valid else 0
        )
        assert metrics.instrumentation_failure_count == int(sample is not None and not valid)

    asyncio.run(scenario())


def test_four_worker_bound_and_fast_scope_progress_while_first_scope_blocks() -> None:
    async def scenario() -> None:
        release_first = asyncio.Event()
        release_wave = asyncio.Event()
        first_wave = asyncio.Event()
        fast_done = asyncio.Event()
        active = peak = finished = 0

        async def hook(scope: RepositoryScope) -> None:
            nonlocal active, peak, finished
            active += 1
            peak = max(peak, active)
            if active == 4:
                first_wave.set()
            try:
                await release_wave.wait()
                if scope.repository_id == 1:
                    await release_first.wait()
                else:
                    finished += 1
                    if finished == 15:
                        fast_done.set()
            finally:
                active -= 1

        store = _Store(hook=hook)
        service = delivery.CiHistoryDeliveryService(store, RuntimeMetrics())
        async with asyncio.timeout(2), asyncio.TaskGroup() as group:
            task = group.create_task(service.run(asyncio.Event()))
            await first_wave.wait()
            assert len(store.calls) == peak == 4
            release_wave.set()
            await fast_done.wait()
            assert not task.done() and len(store.calls) == 16
            assert active == 1 and peak == 4
            release_first.set()
        assert task.result() == ("applied",) * 16

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["before_list", "after_list", "first_scope"])
def test_observed_abort_prevents_the_next_effect(stage: str) -> None:
    async def scenario() -> None:
        abort = asyncio.Event()

        async def listing() -> None:
            if stage == "after_list":
                abort.set()

        async def hook(_scope: RepositoryScope) -> None:
            abort.set()

        store = _Store(hook=hook, list_hook=listing)
        service = delivery.CiHistoryDeliveryService(store, RuntimeMetrics())
        if stage == "before_list":
            abort.set()
        result = await service.run(abort)
        assert result[-1] == "aborted"
        assert store.listings == (0 if stage == "before_list" else 1)
        assert len(store.calls) == (1 if stage == "first_scope" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error",
    [
        CiEconomicsStoreUnavailable("private"),
        RuntimeError("private"),
        TimeoutError("unowned timeout"),
    ],
)
def test_handled_scope_failure_does_not_cancel_later_scopes(error: Exception) -> None:
    async def scenario() -> None:
        async def hook(scope: RepositoryScope) -> None:
            if scope.repository_id == 1:
                raise error

        store = _Store(scopes=_scopes(2), hook=hook)
        result = await delivery.CiHistoryDeliveryService(store, RuntimeMetrics()).run(
            asyncio.Event()
        )
        expected = (
            "store_unavailable"
            if isinstance(error, CiEconomicsStoreUnavailable)
            else "unexpected_error"
        )
        assert result == (expected, "applied") and store.calls == list(_scopes(2))

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["scope", "round", "cancel"])
def test_owned_deadline_and_cancellation_wait_for_worker_cleanup(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def block() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def hook(_scope: RepositoryScope) -> None:
            await block()

        store = _Store(scopes=_scopes(1), list_hook=block if stage == "round" else None, hook=hook)
        service = delivery.CiHistoryDeliveryService(store, RuntimeMetrics())
        if stage in {"scope", "round"}:
            monkeypatch.setattr(
                delivery,
                "HISTORY_DELIVERY_SCOPE_SECONDS"
                if stage == "scope"
                else "HISTORY_DELIVERY_ROUND_SECONDS",
                0.01,
            )
        async with asyncio.timeout(2), asyncio.TaskGroup() as group:
            task = group.create_task(service.run(asyncio.Event()))
            await started.wait()
            if stage == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert await task == ("timed_out",)
        assert stopped.is_set()
        assert len(store.calls) == (0 if stage == "round" else 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scopes", [_scopes(257), (_scopes(1)[0],) * 2, tuple(reversed(_scopes(2))), (object(),)]
)
def test_malformed_scope_population_never_reaches_transfer(scopes: tuple[object, ...]) -> None:
    async def scenario() -> None:
        store = _Store(scopes=cast(tuple[RepositoryScope, ...], scopes))
        result = await delivery.CiHistoryDeliveryService(store, RuntimeMetrics()).run(
            asyncio.Event()
        )
        assert result == ("store_unavailable",) and store.calls == []

    asyncio.run(scenario())
