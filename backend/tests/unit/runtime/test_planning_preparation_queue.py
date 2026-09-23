from __future__ import annotations

import asyncio

import pytest
from preparation_support import preparation_seed
from prometheus_support import prometheus_samples

from ci_coordinator.app.planning_preparation import PlanningPreparationOutcome
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.repo_context.diff_model import RepositoryEpoch
from ci_coordinator.runtime import planning_preparation
from ci_coordinator.runtime.planning_preparation import PlanningPreparationQueue


def test_bounded_nonblocking_queue_coalesces_and_closes_pending_work() -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        seen: list[RepositoryEpoch] = []
        closed: list[bool] = []

        async def prepare(epoch: RepositoryEpoch) -> PlanningPreparationOutcome:
            seen.append(epoch)
            return "prepared"

        queue = PlanningPreparationQueue(prepare, lambda: closed.append(True), metrics)
        assert not queue.offer(preparation_seed())
        await queue.prepare()
        for index in range(32):
            assert queue.offer(preparation_seed(index))
        assert queue.offer(preparation_seed())
        assert not queue.offer(preparation_seed(32))
        await queue.aclose()
        assert seen == []
        assert closed == [True]
        assert not queue.offer(preparation_seed())
        with pytest.raises(RuntimeError, match="twice"):
            await queue.prepare()
        for outcome, expected in (("queued", 32), ("coalesced", 1), ("saturated", 1)):
            assert (
                prometheus_samples(metrics)[
                    ("ci_coordinator_planning_preparation_total", (("outcome", outcome),))
                ]
                == expected
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["exception", "timeout"])
def test_failed_attempt_releases_its_slot_and_later_work_progresses(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(planning_preparation, "_ATTEMPT_SECONDS", 0.02)

    async def scenario() -> None:
        completed = asyncio.Event()
        metrics = RuntimeMetrics()
        seen: list[RepositoryEpoch] = []

        async def prepare(epoch: RepositoryEpoch) -> PlanningPreparationOutcome:
            seen.append(epoch)
            if len(seen) == 1:
                if failure == "exception":
                    raise RuntimeError("provider credential must not leak")
                await asyncio.Event().wait()
            completed.set()
            return "prepared"

        queue = PlanningPreparationQueue(prepare, lambda: None, metrics)
        await queue.prepare()
        assert queue.offer(preparation_seed())
        assert queue.offer(preparation_seed(1))
        await asyncio.wait_for(completed.wait(), 1)
        await queue.aclose()
        assert [epoch.ref for epoch in seen] == ["refs/heads/0", "refs/heads/1"]
        outcome = "failed" if failure == "exception" else "timed_out"
        assert (
            prometheus_samples(metrics)[
                ("ci_coordinator_planning_preparation_total", (("outcome", outcome),))
            ]
            == 1
        )
        assert b"credential" not in metrics.snapshot().content

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["not_started", "idle", "active"])
def test_shutdown_quiesces_worker_before_cache_close(state: str) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        effects: list[str] = []

        async def prepare(_: RepositoryEpoch) -> PlanningPreparationOutcome:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                effects.append("worker_stopped")
            return "prepared"

        queue = PlanningPreparationQueue(
            prepare, lambda: effects.append("cache_closed"), RuntimeMetrics()
        )
        if state != "not_started":
            await queue.prepare()
        if state == "active":
            assert queue.offer(preparation_seed())
            await asyncio.wait_for(started.wait(), 1)
        await queue.aclose()
        assert effects == (
            ["worker_stopped", "cache_closed"] if state == "active" else ["cache_closed"]
        )
        assert not queue.offer(preparation_seed())

    asyncio.run(scenario())


def test_metrics_allow_only_bounded_outcomes() -> None:
    metrics = RuntimeMetrics()
    metrics.planning_preparation("refs/heads/private-branch-secret")
    samples = prometheus_samples(metrics)
    labels = [
        labels for (name, labels) in samples if name == "ci_coordinator_planning_preparation_total"
    ]
    assert len(labels) == 16
    assert all(len(label) == 1 and label[0][0] == "outcome" for label in labels)
    assert samples[("ci_coordinator_planning_preparation_total", (("outcome", "other"),))] == 1
    assert b"private-branch-secret" not in metrics.snapshot().content
