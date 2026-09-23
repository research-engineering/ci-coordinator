import asyncio
from collections.abc import Iterator
from inspect import Parameter, signature

import pytest
from prometheus_client import Histogram
from prometheus_client import context_managers as timing
from prometheus_support import prometheus_samples

from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.observability.runtime_metrics import _StageTimer


def sample(metrics: RuntimeMetrics, lane: str, stage: str, outcome: str, suffix: str) -> float:
    return prometheus_samples(metrics)[
        (
            "ci_coordinator_ci_history_stage_duration_seconds_" + suffix,
            (("lane", lane), ("outcome", outcome), ("stage", stage)),
        )
    ]


def test_stage_timer_exit_contract_matches_positional_prometheus_timer() -> None:
    parameters = signature(_StageTimer.__exit__).parameters
    assert parameters["exception_type"].kind is Parameter.POSITIONAL_ONLY
    assert parameters["exception"].kind is Parameter.POSITIONAL_ONLY
    assert parameters["traceback"].kind is Parameter.POSITIONAL_ONLY


@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError, BaseException])
def test_stage_records_exact_elapsed_and_preserves_body_outcome(
    monkeypatch: pytest.MonkeyPatch, failure: type[BaseException] | None
) -> None:
    metrics = RuntimeMetrics()
    times = iter((10.0, 10.25))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(timing, "default_timer", times.__next__)
    error = None if failure is None else failure("private body detail")
    calls = 0
    try:
        with metrics.ci_history_stage("backfill", "provider"):
            calls += 1
            if error is not None:
                raise error
    except BaseException as caught:
        assert caught is error
    else:
        assert error is None
    outcome = (
        "returned"
        if error is None
        else "cancelled"
        if failure is asyncio.CancelledError
        else "raised"
    )
    assert calls == 1
    assert sample(metrics, "backfill", "provider", outcome, "count") == 1
    assert sample(metrics, "backfill", "provider", outcome, "sum") == 0.25
    assert metrics.instrumentation_failure_count == 0
    assert b"private body detail" not in metrics.snapshot().content


@pytest.mark.parametrize("fault", ["factory", "start", "labels", "observation"])
@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
def test_timer_failure_does_not_retry_or_replace_the_body(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    failure: type[BaseException] | None,
) -> None:
    metrics = RuntimeMetrics()
    owner, attribute = {
        "factory": (Histogram, "time"),
        "start": (timing.Timer, "__enter__"),
        "labels": (timing.Timer, "labels"),
        "observation": (Histogram, "observe"),
    }[fault]

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private timer detail")

    monkeypatch.setattr(owner, attribute, fail)
    error = None if failure is None else failure("original")
    calls = 0
    try:
        with metrics.ci_history_stage("recent", "completion"):
            calls += 1
            if error is not None:
                raise error
    except BaseException as caught:
        assert caught is error
    else:
        assert error is None
    assert calls == 1
    assert metrics.instrumentation_failure_count == 1
    assert b"private timer detail" not in metrics.snapshot().content


async def test_overlapping_spans_keep_their_own_clock_and_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = RuntimeMetrics()
    times: Iterator[float] = iter((1.0, 2.0, 4.0, 8.0))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(timing, "default_timer", times.__next__)
    first_started, second_started, first_finished = (asyncio.Event() for _ in range(3))

    async def first() -> None:
        with metrics.ci_history_stage("backfill", "claim"):
            first_started.set()
            await second_started.wait()
        first_finished.set()

    async def second() -> None:
        await first_started.wait()
        with metrics.ci_history_stage("repair", "provider"):
            second_started.set()
            await first_finished.wait()

    async with asyncio.TaskGroup() as group:
        group.create_task(first())
        group.create_task(second())
    assert sample(metrics, "backfill", "claim", "returned", "sum") == 3
    assert sample(metrics, "repair", "provider", "returned", "sum") == 6
    assert sample(metrics, "backfill", "claim", "returned", "count") == 1
    assert sample(metrics, "repair", "provider", "returned", "count") == 1


def test_stage_metric_bounds_both_untrusted_labels() -> None:
    metrics = RuntimeMetrics()
    with metrics.ci_history_stage("private-repository", "private-operation"):
        pass
    assert sample(metrics, "other", "other", "returned", "count") == 1
    assert b"private-" not in metrics.snapshot().content


def test_failure_after_observation_does_not_claim_atomic_sample_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = RuntimeMetrics()
    observe = Histogram.observe

    def observe_then_fail(histogram: Histogram, amount: float) -> None:
        observe(histogram, amount)
        raise RuntimeError("exporter failure after update")

    monkeypatch.setattr(Histogram, "observe", observe_then_fail)
    with metrics.ci_history_stage("backfill", "completion"):
        value = "applied"
    assert value == "applied"
    assert sample(metrics, "backfill", "completion", "returned", "count") == 1
    assert metrics.instrumentation_failure_count == 1
