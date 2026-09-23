import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest
from app._history_collection_support import (
    CLAIM,
    DATASET,
    PAGE,
    PENDING_CLAIM,
    STATISTICS,
    CollectionBoundary,
    recheck_claim,
)

from ci_coordinator.app import ci_history_collection
from ci_coordinator.app.ci_history_collection import (
    HISTORY_LANES,
    HISTORY_WORK_LANES,
    HistoryLane,
    HistoryWorkLane,
)
from ci_coordinator.ci_economics.collection import ProviderCollectionFailureReason
from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_ports import HistoryAttemptNotFound, HistoryTransition
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable


@pytest.mark.parametrize(
    "worker_id", [None, True, b"a" * 64, "a" * 63, "a" * 65, "A" * 64, "g" * 64]
)
def test_history_worker_identity_is_rejected_before_effects(worker_id: object) -> None:
    boundary = CollectionBoundary()
    with pytest.raises(ValueError):
        boundary.service(worker_id=cast(str, worker_id))
    assert boundary.calls == []


async def test_page_read_preserves_exact_request_and_durable_result() -> None:
    boundary = CollectionBoundary()
    assert await boundary.run() == ("applied", "none_due", "none_due")
    assert ("discover", (DATASET.scope, CLAIM.state.checkpoint.cursor.window, 1)) in boundary.calls
    assert ("page", (CLAIM, PAGE)) in boundary.calls
    assert boundary.count("provider_results", "backfill", "page") == 1
    assert boundary.count("items", "backfill", "applied") == 1


async def test_empty_item_claims_each_lane_once_without_provider_or_store_effects() -> None:
    boundary = CollectionBoundary(history=None)
    service = boundary.service()
    for lane in HISTORY_WORK_LANES:
        assert await service.collect_next(lane, asyncio.Event()) == "none_due"
    assert sorted(boundary.calls) == [
        ("claim_backfill", "a" * 64),
        ("claim_discovery", "a" * 64),
        ("claim_recent", "a" * 64),
        ("claim_repair", "a" * 64),
    ]
    for lane in ("backfill", "discovery", "recent", "repair"):
        assert boundary.count("items", lane, "none_due") == 1


@pytest.mark.parametrize("lane", HISTORY_LANES)
@pytest.mark.parametrize("transition", ["applied", "claim_lost", "capacity_reached"])
async def test_exact_attempt_collection_preserves_storage_transition(
    lane: HistoryLane, transition: HistoryTransition
) -> None:
    boundary = CollectionBoundary.for_lane(lane)
    boundary.transition = transition
    outcomes = await boundary.run()
    assert outcomes[HISTORY_LANES.index(lane)] == transition
    assert len([name for name, _ in boundary.calls if name == "read_attempt"]) == 1
    reads = [value for name, value in boundary.calls if name == "read_attempt"]
    pending = PENDING_CLAIM.state.checkpoint.pending
    assert pending is not None
    expected_cursor = (
        pending.attempt_cursor if lane == "backfill" else recheck_claim(lane).state.cursor
    )
    assert reads == [(expected_cursor, STATISTICS.run_created_at)]
    assert boundary.count("provider_results", lane, "complete") == 1
    assert boundary.count("items", lane, transition) == 1
    expected = (
        (PENDING_CLAIM, STATISTICS) if lane == "backfill" else (recheck_claim(lane), STATISTICS)
    )
    assert (
        "statistics" if lane == "backfill" else "recheck_statistics",
        expected,
    ) in boundary.calls


@pytest.mark.parametrize("lane", HISTORY_LANES)
@pytest.mark.parametrize(
    "reason",
    [
        "provider_unavailable",
        "provider_malformed",
        "provider_binding_mismatch",
        "provider_incomplete",
        "provider_not_terminal",
        "provider_unstable",
    ],
)
async def test_successful_deferral_does_not_mask_provider_failure(
    lane: HistoryLane, reason: ProviderCollectionFailureReason
) -> None:
    boundary = CollectionBoundary.for_lane(lane)
    boundary.attempt = ProviderAttemptDeferred(reason)
    assert (await boundary.run())[HISTORY_LANES.index(lane)] == "applied"
    assert boundary.count("provider_results", lane, reason) == 1
    assert boundary.count("provider_results", lane, "complete") == 0
    assert not any(name in {"statistics", "recheck_statistics"} for name, _ in boundary.calls)
    if lane == "backfill":
        stored_reason = (
            "provider_malformed"
            if reason in {"provider_malformed", "provider_binding_mismatch", "provider_incomplete"}
            else "provider_unavailable"
        )
        assert ("defer", (PENDING_CLAIM, stored_reason)) in boundary.calls
    elif reason == "provider_not_terminal":
        assert ("waiting", recheck_claim(lane)) in boundary.calls
    else:
        assert ("recheck_failure", (recheck_claim(lane), False)) in boundary.calls


@pytest.mark.parametrize("lane", HISTORY_LANES)
@pytest.mark.parametrize("missing_matches", [True, False])
async def test_only_exact_missing_attempt_can_advance_as_unavailable(
    lane: HistoryLane, missing_matches: bool
) -> None:
    boundary = CollectionBoundary.for_lane(lane)
    pending = PENDING_CLAIM.state.checkpoint.pending
    assert pending is not None
    cursor = pending.attempt_cursor
    boundary.attempt = HistoryAttemptNotFound(
        cursor if missing_matches else replace(cursor, workflow_run_id=304)
    )
    await boundary.run()
    outcome = "unavailable_attempt" if missing_matches else "provider_binding_mismatch"
    assert boundary.count("provider_results", lane, outcome) == 1
    if lane == "backfill":
        assert any(name == "missing" for name, _ in boundary.calls) is missing_matches
    else:
        assert ("recheck_failure", (recheck_claim(lane), missing_matches)) in boundary.calls


@pytest.mark.parametrize("lane", HISTORY_LANES)
@pytest.mark.parametrize(
    "operand",
    [
        "installation_id",
        "repository_id",
        "workflow_run_id",
        "run_attempt",
        "workflow_id",
        "run_created_at",
    ],
)
async def test_independent_attempt_identity_operands_cannot_reach_statistics_writer(
    lane: HistoryLane, operand: str
) -> None:
    boundary = CollectionBoundary.for_lane(lane)
    if operand == "workflow_id":
        boundary.attempt = STATISTICS.model_copy(update={operand: 405})
    elif operand == "run_created_at":
        boundary.attempt = STATISTICS.model_copy(
            update={operand: STATISTICS.run_created_at + timedelta(seconds=1)}
        )
    else:
        attempt = STATISTICS.attempt.model_copy(
            update={operand: getattr(STATISTICS.attempt, operand) + 1}
        )
        boundary.attempt = STATISTICS.model_copy(update={"attempt": attempt})
    await boundary.run()
    assert boundary.count("provider_results", lane, "provider_binding_mismatch") == 1
    assert not any(name in {"statistics", "recheck_statistics"} for name, _ in boundary.calls)


@pytest.mark.parametrize("authority", ["denied", "malformed", "unavailable"])
async def test_current_access_admission_precedes_all_provider_io(authority: str) -> None:
    boundary = CollectionBoundary(allowed=cast(bool, 1) if authority == "malformed" else False)
    if authority == "unavailable":
        boundary.errors["access"] = RepositoryAccessUnavailable("private")
    await boundary.run()
    assert not any(name in {"discover", "read_attempt", "page"} for name, _ in boundary.calls)
    assert ("defer", (CLAIM, "access_unavailable")) in boundary.calls
    assert boundary.count("provider_results", "backfill", "access_unavailable") == 1


@pytest.mark.parametrize("operand", ["scope", "window", "page_number"])
async def test_foreign_page_cannot_advance_history(operand: str) -> None:
    page = PAGE.page
    if operand == "scope":
        foreign = replace(
            page, scope=replace(page.scope, installation_id=102), sources=(), provider_total=0
        )
    elif operand == "window":
        foreign = replace(
            page,
            window=replace(
                page.window, created_from=page.window.created_from - timedelta(seconds=1)
            ),
        )
    else:
        foreign = replace(page, page_number=2, provider_total=101)
    boundary = CollectionBoundary(
        page=ProviderObservationPage(foreign, () if not foreign.sources else (404,))
    )
    await boundary.run()
    assert ("defer", (CLAIM, "provider_malformed")) in boundary.calls
    assert not any(name == "page" for name, _ in boundary.calls)
    assert boundary.count("provider_results", "backfill", "provider_binding_mismatch") == 1


async def test_excluded_workflow_uses_checked_skip_without_reading_provider() -> None:
    dataset = replace(
        DATASET, configuration=DATASET.configuration.model_copy(update={"workflow_ids": (405,)})
    )
    boundary = CollectionBoundary(history=(dataset, PENDING_CLAIM))
    await boundary.run()
    assert ("skip", PENDING_CLAIM) in boundary.calls
    assert not any(name in {"access", "read_attempt", "statistics"} for name, _ in boundary.calls)


@pytest.mark.parametrize(
    "dataset",
    [
        replace(DATASET, scope=replace(DATASET.scope, installation_id=102)),
        replace(DATASET, generation=2),
        replace(DATASET, configuration_revision=2),
        replace(
            DATASET,
            state="paused",
            configuration=DATASET.configuration.model_copy(update={"enabled": False}),
        ),
    ],
    ids=["scope", "generation", "configuration_revision", "state"],
)
async def test_inconsistent_claim_carrier_is_not_used(dataset: HistoryDataset) -> None:
    boundary = CollectionBoundary(history=(dataset, CLAIM))
    assert (await boundary.run())[0] == "store_unavailable"
    assert not any(name in {"access", "discover", "page", "defer"} for name, _ in boundary.calls)


@pytest.mark.parametrize(
    ("lane", "page_read"),
    [("backfill", True), ("backfill", False), ("recent", False), ("repair", False)],
)
@pytest.mark.parametrize("stage", ["before", "claim", "access", "provider"])
async def test_observed_abort_prevents_subsequent_external_effects(
    lane: HistoryLane, page_read: bool, stage: str
) -> None:
    abort = asyncio.Event()
    provider = "discover" if page_read else "read_attempt"
    trigger = {
        "before": "before",
        "claim": "claim_" + lane,
        "access": "access",
        "provider": provider,
    }[stage]

    async def stop(current: str) -> None:
        if current == trigger:
            abort.set()

    boundary = CollectionBoundary.for_lane(lane)
    if page_read:
        boundary.history = (DATASET, CLAIM)
    boundary.hook = stop
    if stage == "before":
        abort.set()
    outcomes = await boundary.run(abort)
    assert outcomes[HISTORY_LANES.index(lane)] == "aborted"
    assert [name for name, _ in boundary.calls if not name.startswith("claim_")] == {
        "before": [],
        "claim": [],
        "access": ["access"],
        "provider": ["access", provider],
    }[stage]
    if stage == "before":
        assert boundary.calls == []
    assert boundary.count("items", lane, "applied") == 0


@pytest.mark.parametrize("stage", ["access", "discover", "read_attempt"])
async def test_whole_provider_deadline_includes_authorization_and_preserves_timeout_origin(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ci_history_collection, "HISTORY_PROVIDER_DEADLINE_SECONDS", 0.01)

    async def block(current: str) -> None:
        if current == stage:
            await asyncio.Event().wait()

    boundary = CollectionBoundary(
        history=(DATASET, PENDING_CLAIM) if stage == "read_attempt" else (DATASET, CLAIM),
        hook=block,
    )
    async with asyncio.timeout(3):
        await boundary.run()
    assert boundary.count("provider_results", "backfill", "timed_out") == 1
    boundary.hook = None
    boundary.errors[stage] = TimeoutError("inner provider timeout")
    await boundary.run()
    assert boundary.count("provider_results", "backfill", "timed_out") == 1
    assert boundary.count("provider_results", "backfill", "provider_unavailable") == 1


@pytest.mark.parametrize(
    ("lane", "page_read"),
    [("backfill", True), ("backfill", False), ("recent", False), ("repair", False)],
)
async def test_access_and_provider_share_one_cumulative_monotonic_deadline(
    lane: HistoryLane, page_read: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    original_time = loop.time
    offset = 0.0

    async def consume(stage: str) -> None:
        nonlocal offset
        if stage in {"access", "discover", "read_attempt"}:
            offset += 6.0
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    boundary = CollectionBoundary.for_lane(lane)
    if page_read:
        boundary.history = (DATASET, CLAIM)
    boundary.hook = consume
    with monkeypatch.context() as clock:
        clock.setattr(ci_history_collection, "HISTORY_PROVIDER_DEADLINE_SECONDS", 10)
        clock.setattr(loop, "time", lambda: original_time() + offset)
        assert (await boundary.run())[HISTORY_LANES.index(lane)] == "applied"
    assert offset == 12.0
    assert boundary.count("provider_results", lane, "timed_out") == 1
    assert not any(
        name in {"page", "statistics", "recheck_statistics"} for name, _ in boundary.calls
    )


async def test_three_lanes_are_concurrent_bounded_and_do_not_wait_for_backfill_completion() -> None:
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = 0

    async def barrier(stage: str) -> None:
        nonlocal writes
        if stage == "discover":
            entered.set()
            await release.wait()
        if stage == "recheck_statistics":
            writes += 1
            if writes == 2:
                completed.set()

    boundary = CollectionBoundary(
        rechecks={lane: recheck_claim(lane) for lane in ("recent", "repair")}, hook=barrier
    )
    async with asyncio.timeout(3):
        task = asyncio.create_task(boundary.run())
        try:
            await entered.wait()
            await completed.wait()
            await asyncio.sleep(0)
            assert not task.done()
            assert not any(name == "page" for name, _ in boundary.calls)
            assert boundary.count("items", "recent", "applied") == 1
            assert boundary.count("items", "repair", "applied") == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert await task == ("applied", "applied", "applied")
    assert len([name for name, _ in boundary.calls if name in {"discover", "read_attempt"}]) == 3
    assert len([name for name, _ in boundary.calls if name.startswith("claim_")]) == 3


@pytest.mark.parametrize("error", [CiEconomicsStoreUnavailable("private"), RuntimeError("private")])
async def test_one_lane_failure_does_not_cancel_other_lanes(error: Exception) -> None:
    boundary = CollectionBoundary(
        rechecks={"recent": recheck_claim("recent")}, errors={"claim_backfill": error}
    )
    result = await boundary.run()
    assert result == (
        "store_unavailable"
        if isinstance(error, CiEconomicsStoreUnavailable)
        else "unexpected_error",
        "applied",
        "none_due",
    )
    assert boundary.count("items", "recent", "applied") == 1


@pytest.mark.parametrize("stage", ["claim_backfill", "access", "discover", "page", "defer"])
async def test_external_cancellation_propagates_without_success(stage: str) -> None:
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def block(current: str) -> None:
        if stage == current:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    boundary = CollectionBoundary(
        hook=block,
        page=ProviderAttemptDeferred("provider_unavailable") if stage == "defer" else PAGE,
    )
    async with asyncio.timeout(3):
        task = asyncio.create_task(boundary.run())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set()
    assert boundary.count("items", "backfill", "aborted") == 1
    assert boundary.count("items", "backfill", "applied") == 0


def test_history_metrics_bound_untrusted_labels() -> None:
    boundary = CollectionBoundary()
    boundary.metrics.ci_history_item("private-repo", "private-error")
    boundary.metrics.ci_history_provider_result("private-repo", "private-error")
    assert boundary.count("items", "other", "other") == 1
    assert boundary.count("provider_results", "other", "other") == 1


@pytest.mark.parametrize("lane", [None, True, 1, [], "unknown"])
async def test_unadmitted_lane_is_rejected_before_collection_effects(lane: object) -> None:
    boundary = CollectionBoundary()
    with pytest.raises(ValueError, match="admitted lane"):
        await boundary.service().collect_next(cast(HistoryWorkLane, lane), asyncio.Event())
    assert boundary.calls == []
