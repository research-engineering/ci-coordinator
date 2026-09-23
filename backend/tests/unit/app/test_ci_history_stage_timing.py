import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from app._history_collection_support import (
    CLAIM,
    DATASET,
    PENDING_CLAIM,
    CollectionBoundary,
    recheck_claim,
)
from prometheus_support import prometheus_samples

from ci_coordinator.app.ci_history_collection import (
    HISTORY_LANES,
    HISTORY_WORK_LANES,
    HistoryLane,
    HistoryWorkLane,
)
from ci_coordinator.ci_economics.history_ports import HistoryAttemptNotFound
from ci_coordinator.ci_economics.history_scan import HistoryClaim, RecentHistoryProgress
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred


def stage_counts(boundary: CollectionBoundary, lane: str) -> dict[tuple[str, str], float]:
    return {
        (dict(labels)["stage"], dict(labels)["outcome"]): count
        for (name, labels), count in prometheus_samples(boundary.metrics).items()
        if name == "ci_coordinator_ci_history_stage_duration_seconds_count"
        and dict(labels)["lane"] == lane
    }


def collection(lane: HistoryWorkLane) -> CollectionBoundary:
    if lane != "discovery":
        return CollectionBoundary.for_lane(lane)
    prior = CLAIM.state
    claim = HistoryClaim(
        replace(prior, recent=RecentHistoryProgress(prior.checkpoint.cursor.created_from))
    )
    return CollectionBoundary(history=None, discovery_history=(DATASET, claim))


@pytest.mark.parametrize("lane", HISTORY_WORK_LANES)
@pytest.mark.parametrize("mode", ["page_or_attempt", "denied", "deferred", "idle"])
async def test_each_lane_times_only_its_actual_operations(lane: HistoryWorkLane, mode: str) -> None:
    boundary = collection(lane)
    if mode == "idle":
        boundary.history = boundary.discovery_history = None
        boundary.rechecks = {}
    elif mode == "denied":
        boundary.allowed = False
    elif mode == "deferred":
        boundary.page = boundary.attempt = ProviderAttemptDeferred("provider_unavailable")
    result = await boundary.service().collect_next(lane, asyncio.Event())
    expected = {("claim", "returned"): 1.0}
    if mode != "idle":
        expected.update({("access", "returned"): 1.0, ("completion", "returned"): 1.0})
        if mode != "denied":
            expected[("provider", "returned")] = 1.0
    assert stage_counts(boundary, lane) == expected
    assert boundary.count("items", lane, result) == 1
    assert sum(name == "claim_" + lane for name, _ in boundary.calls) == 1


@pytest.mark.parametrize("mode", ["handoff", "skip"])
async def test_local_completion_has_no_fabricated_provider_span(mode: str) -> None:
    if mode == "handoff":
        prior = PENDING_CLAIM.state
        claim = HistoryClaim(
            replace(prior, recent=RecentHistoryProgress(prior.checkpoint.cursor.created_from))
        )
        lane: HistoryWorkLane = "discovery"
        boundary = CollectionBoundary(history=None, discovery_history=(DATASET, claim))
    else:
        dataset = replace(
            DATASET, configuration=DATASET.configuration.model_copy(update={"workflow_ids": (405,)})
        )
        lane = "backfill"
        boundary = CollectionBoundary(history=(dataset, PENDING_CLAIM))
    assert await boundary.service().collect_next(lane, asyncio.Event()) == "applied"
    assert stage_counts(boundary, lane) == {
        ("claim", "returned"): 1,
        ("completion", "returned"): 1,
    }
    assert [name for name, _ in boundary.calls] == ["claim_" + lane, mode]


@pytest.mark.parametrize(
    ("operation", "stage"),
    [
        ("claim_backfill", "claim"),
        ("access", "access"),
        ("discover", "provider"),
        ("page", "completion"),
    ],
)
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failure_or_cancellation_keeps_the_failing_stage_visible(
    operation: str, stage: str, cancelled: bool
) -> None:
    error = asyncio.CancelledError() if cancelled else CiEconomicsStoreUnavailable()
    boundary = CollectionBoundary(errors={operation: error})
    if cancelled:
        with pytest.raises(asyncio.CancelledError) as caught:
            await boundary.service().collect_next("backfill", asyncio.Event())
        assert caught.value is error
    else:
        assert (
            await boundary.service().collect_next("backfill", asyncio.Event())
            == "store_unavailable"
        )
    counts = stage_counts(boundary, "backfill")
    assert counts[(stage, "cancelled" if cancelled else "raised")] == 1
    assert (stage, "returned") not in counts
    assert sum(name == operation for name, _ in boundary.calls) == 1


@pytest.mark.parametrize("lane", HISTORY_WORK_LANES)
@pytest.mark.parametrize("after", [None, "claim", "access", "provider"])
async def test_abort_records_only_the_actual_operation_prefix(
    lane: HistoryWorkLane, after: str | None
) -> None:
    boundary = collection(lane)
    abort = asyncio.Event()
    operations = ["claim_" + lane, "access", "discover" if lane == "discovery" else "read_attempt"]
    phases = ["claim", "access", "provider"]
    prefix = [] if after is None else phases[: phases.index(after) + 1]
    if after is None:
        abort.set()
    else:
        target_operation = operations[phases.index(after)]

        async def stop(operation: str) -> None:
            if operation == target_operation:
                abort.set()

        boundary.hook = stop
    assert await boundary.service().collect_next(lane, abort) == "aborted"
    assert stage_counts(boundary, lane) == {(stage, "returned"): 1 for stage in prefix}
    assert [name for name, _ in boundary.calls] == operations[: len(prefix)]
    assert boundary.count("items", lane, "aborted") == 1


@pytest.mark.parametrize("lane", HISTORY_LANES)
async def test_missing_attempt_retains_its_completion_span(lane: HistoryLane) -> None:
    boundary = collection(lane)
    pending = PENDING_CLAIM.state.checkpoint.pending
    assert pending is not None
    claim = PENDING_CLAIM if lane == "backfill" else recheck_claim(lane)
    cursor = pending.attempt_cursor if lane == "backfill" else recheck_claim(lane).state.cursor
    boundary.attempt = HistoryAttemptNotFound(cursor)
    assert await boundary.service().collect_next(lane, asyncio.Event()) == "applied"
    assert stage_counts(boundary, lane) == {
        (stage, "returned"): 1 for stage in ("claim", "access", "provider", "completion")
    }
    expected = ("missing", claim) if lane == "backfill" else ("recheck_failure", (claim, True))
    assert boundary.calls[-1] == expected


@pytest.mark.parametrize("lane", ["recent", "repair"])
async def test_waiting_attempt_retains_its_completion_span(
    lane: Literal["recent", "repair"],
) -> None:
    boundary = collection(lane)
    boundary.attempt = ProviderAttemptDeferred("provider_not_terminal")
    assert await boundary.service().collect_next(lane, asyncio.Event()) == "applied"
    assert stage_counts(boundary, lane) == {
        (stage, "returned"): 1 for stage in ("claim", "access", "provider", "completion")
    }
    assert boundary.calls[-1] == ("waiting", recheck_claim(lane))
