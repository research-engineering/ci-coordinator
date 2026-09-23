from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration
from ci_coordinator.ci_economics.history_rechecks import (
    HistoryRecheckClaim,
    HistoryRecheckHint,
    HistoryRecheckSource,
    HistoryRecheckState,
    acquire_history_recheck,
    current_history_recheck,
    finish_history_recheck,
    initial_history_recheck,
    merge_history_recheck_hint,
    recheck_capacity_available,
    recover_exhausted_history_recheck,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .archive_factories import ARCHIVE_TIME, history_dataset


def _hint(
    first: int = 1, latest: int = 4, source: HistoryRecheckSource = "recent"
) -> HistoryRecheckHint:
    return HistoryRecheckHint(
        HistoryAttemptCursor(history_dataset().scope, 303, latest, first),
        404,
        ARCHIVE_TIME - timedelta(days=40),
        source,
    )


def _initial(hint: HistoryRecheckHint | None = None) -> HistoryRecheckState:
    state = initial_history_recheck(history_dataset(), hint or _hint(), ARCHIVE_TIME)
    assert state is not None
    return state


def _claim(state: HistoryRecheckState | None = None) -> HistoryRecheckClaim:
    state = state or _initial()
    claim = acquire_history_recheck(
        history_dataset(), state, now=state.next_attempt_at, worker_id="a" * 64, token="b" * 64
    )
    assert claim is not None
    return claim


def test_duplicate_hint_does_not_rewind_completed_progress_or_reset_retry() -> None:
    first = _claim()
    completed = finish_history_recheck(
        history_dataset(), first.state, first, now=ARCHIVE_TIME, outcome="recorded"
    )
    assert completed is not None and completed.successor is not None
    assert completed.successor.next_attempt == 2
    second = _claim(completed.successor)
    deferred = finish_history_recheck(
        history_dataset(),
        second.state,
        second,
        now=ARCHIVE_TIME,
        outcome="deferred",
        jitter_seconds=15,
    )
    assert deferred is not None and deferred.successor is not None
    state = deferred.successor
    assert state.next_attempt_at == ARCHIVE_TIME + timedelta(seconds=75)
    assert state.acquisition_count == 1
    for hint in (_hint(), _hint(first=2, latest=3), _hint(latest=2, source="repair")):
        assert merge_history_recheck_hint(state, hint) == state


@pytest.mark.parametrize("change", ["upper", "source"])
def test_real_same_attempt_hint_change_preserves_budget_deadline_and_recovery_bound(
    change: str,
) -> None:
    claim = _claim(_initial(_hint(source="repair")))
    hint = _hint(latest=5, source="repair") if change == "upper" else _hint()
    state = merge_history_recheck_hint(claim.state, hint)
    assert state.revision == claim.state.revision + 1
    assert state.acquisition_count == claim.state.acquisition_count
    assert state.next_attempt == claim.state.next_attempt
    assert state.next_attempt_at == claim.state.next_attempt_at
    assert state.lease == claim.state.lease
    assert not current_history_recheck(history_dataset(), state, claim, ARCHIVE_TIME)
    assert (
        acquire_history_recheck(
            history_dataset(), state, now=ARCHIVE_TIME, worker_id="c" * 64, token="d" * 64
        )
        is None
    )


def test_new_earlier_population_can_rewind_and_get_its_own_budget() -> None:
    claim = _claim(_initial(_hint(first=3)))
    state = merge_history_recheck_hint(claim.state, _hint(first=2))
    assert state.hint.cursor.next_attempt == state.next_attempt == 2
    assert state.acquisition_count == 0 and state.lease is None
    assert state.next_attempt_at == claim.state.next_attempt_at
    assert not current_history_recheck(history_dataset(), state, claim, ARCHIVE_TIME)
    assert merge_history_recheck_hint(state, _hint(first=3)) == state


@pytest.mark.parametrize("attempt", [1, 2, 3, 4])
def test_parent_bound_repair_revisits_only_its_transferred_completed_attempt(attempt: int) -> None:
    claim = _claim(replace(_initial(), next_attempt=3))
    hint = _hint(first=attempt, latest=attempt, source="repair")
    assert merge_history_recheck_hint(claim.state, hint) == claim.state
    repaired = merge_history_recheck_hint(claim.state, hint, revisit_completed=True)
    assert repaired.next_attempt == min(attempt, 3)
    assert repaired.hint == claim.state.hint
    assert repaired.next_attempt_at == claim.state.next_attempt_at
    if attempt < 3:
        assert repaired.revision == claim.state.revision + 1
        assert repaired.acquisition_count == 0 and repaired.lease is None
        assert not current_history_recheck(history_dataset(), repaired, claim, ARCHIVE_TIME)
    else:
        assert repaired == claim.state
    assert merge_history_recheck_hint(repaired, hint, revisit_completed=True) == repaired


def test_recent_hint_cannot_request_historical_repair_authority() -> None:
    with pytest.raises(ValueError, match="parent-bound historical repair"):
        merge_history_recheck_hint(_initial(), _hint(), revisit_completed=True)


def test_successful_nonterminal_reads_do_not_exhaust_failure_budget_or_lose_the_attempt() -> None:
    state = _initial()
    for _ in range(5):
        claim = _claim(state)
        waiting = finish_history_recheck(
            history_dataset(),
            claim.state,
            claim,
            now=state.next_attempt_at,
            outcome="waiting",
            jitter_seconds=5,
        )
        assert waiting is not None and waiting.successor is not None
        successor = waiting.successor
        assert waiting.outcome == "waiting" and successor.acquisition_count == 0
        assert successor.next_attempt == state.next_attempt and successor.lease is None
        assert successor.next_attempt_at == state.next_attempt_at + timedelta(seconds=65)
        state = successor
    terminal = _claim(state)
    result = finish_history_recheck(
        history_dataset(), terminal.state, terminal, now=state.next_attempt_at, outcome="recorded"
    )
    assert result is not None and result.successor is not None
    assert result.successor.next_attempt == state.next_attempt + 1


@pytest.mark.parametrize("field", ["scope", "run", "workflow", "creation"])
def test_hint_replay_rejects_each_conflicting_source_operand(field: str) -> None:
    state = _initial()
    hint = state.hint
    if field == "scope":
        hint = replace(hint, cursor=replace(hint.cursor, scope=RepositoryScope(102, 202)))
    elif field == "run":
        hint = replace(hint, cursor=replace(hint.cursor, workflow_run_id=304))
    elif field == "workflow":
        hint = replace(hint, workflow_id=405)
    else:
        hint = replace(hint, run_created_at=hint.run_created_at + timedelta(seconds=1))
    with pytest.raises(ValueError, match="source identity"):
        merge_history_recheck_hint(state, hint)


@pytest.mark.parametrize("outcome", ["recorded", "unavailable"])
def test_last_attempt_is_removed_only_by_current_claim(
    outcome: Literal["recorded", "unavailable"],
) -> None:
    claim = _claim(_initial(_hint(first=4)))
    result = finish_history_recheck(
        history_dataset(), claim.state, claim, now=ARCHIVE_TIME, outcome=outcome
    )
    assert result is not None and result.successor is None and result.outcome == outcome
    assert (
        finish_history_recheck(
            history_dataset(),
            replace(claim.state, revision=claim.state.revision + 1),
            claim,
            now=ARCHIVE_TIME,
            outcome=outcome,
        )
        is None
    )


def test_three_failures_advance_with_explicit_exhaustion_not_fake_statistics() -> None:
    state = _initial()
    for expected_count in (1, 2, 3):
        claim = _claim(state)
        assert claim.state.acquisition_count == expected_count
        result = finish_history_recheck(
            history_dataset(), claim.state, claim, now=state.next_attempt_at, outcome="deferred"
        )
        assert result is not None and result.successor is not None
        if expected_count < 3:
            assert result.outcome == "deferred" and result.successor.next_attempt == 1
            assert result.successor.next_attempt_at == state.next_attempt_at + timedelta(
                seconds=60 * 2 ** (expected_count - 1)
            )
        else:
            assert result.outcome == "retry_exhausted" and result.successor.next_attempt == 2
            assert result.successor.acquisition_count == 0 and result.successor.lease is None
        state = result.successor


def _abandoned_last_claim() -> HistoryRecheckClaim:
    claim = _claim()
    for _ in range(2):
        assert claim.state.lease is not None
        reclaimed = acquire_history_recheck(
            history_dataset(),
            claim.state,
            now=claim.state.lease.expires_at,
            worker_id="c" * 64,
            token="d" * 64,
        )
        assert reclaimed is not None
        claim = reclaimed
    assert claim.state.acquisition_count == 3
    return claim


@pytest.mark.parametrize(
    "offset,holder,recovery", [(-1, True, False), (0, False, True), (1, False, True)]
)
def test_crashes_count_and_expiry_partitions_terminal_holder_from_gap_recovery(
    offset: int, holder: bool, recovery: bool
) -> None:
    claim = _abandoned_last_claim()
    assert claim.state.lease is not None
    now = claim.state.lease.expires_at + timedelta(microseconds=offset)
    assert current_history_recheck(history_dataset(), claim.state, claim, now) is holder
    result = recover_exhausted_history_recheck(history_dataset(), claim.state, now)
    assert (result is not None) is recovery
    assert (
        acquire_history_recheck(
            history_dataset(), claim.state, now=now, worker_id="e" * 64, token="f" * 64
        )
        is None
    )
    if result is not None:
        assert result.outcome == "retry_exhausted" and result.successor is not None
        assert result.successor.next_attempt == 2
        assert not current_history_recheck(history_dataset(), result.successor, claim, now)


def test_upper_extension_cannot_destroy_exhausted_lease_recovery() -> None:
    claim = _abandoned_last_claim()
    state = merge_history_recheck_hint(claim.state, _hint(latest=9))
    assert state.lease is not None
    assert state.acquisition_count == 3
    assert not current_history_recheck(
        history_dataset(), state, claim, state.lease.expires_at - timedelta(microseconds=1)
    )
    result = recover_exhausted_history_recheck(history_dataset(), state, state.lease.expires_at)
    assert result is not None and result.successor is not None
    assert result.successor.cursor.latest_attempt == 9 and result.successor.cursor.next_attempt == 2


def test_exhausted_state_cannot_drop_its_only_recovery_bound() -> None:
    claim = _abandoned_last_claim()
    with pytest.raises(ValueError, match="recovery lease"):
        replace(claim.state, lease=None)


@pytest.mark.parametrize("last", [False, True])
def test_capacity_preserves_work_and_refunds_only_the_current_durable_debit(last: bool) -> None:
    claim = _abandoned_last_claim() if last else _claim()
    assert claim.state.lease is not None
    now = claim.state.lease.acquired_at
    result = finish_history_recheck(
        history_dataset(),
        claim.state,
        claim,
        now=now,
        outcome="capacity_reached",
        jitter_seconds=20,
    )
    assert result is not None and result.successor is not None
    state = result.successor
    assert result.outcome == "capacity_reached"
    assert state.acquisition_count == claim.state.acquisition_count - 1
    assert state.hint == claim.state.hint and state.next_attempt == claim.state.next_attempt
    assert state.next_attempt_at == now + timedelta(seconds=80) and state.lease is None
    assert (
        finish_history_recheck(history_dataset(), state, claim, now=now, outcome="capacity_reached")
        is None
    )
    assert (
        finish_history_recheck(
            history_dataset(),
            claim.state,
            claim,
            now=claim.state.lease.expires_at,
            outcome="capacity_reached",
        )
        is None
    )


def test_compatible_data_only_changes_do_not_cancel_a_current_claim() -> None:
    claim = _claim()
    dataset = replace(history_dataset(), data_revision=2)
    assert current_history_recheck(dataset, claim.state, claim, ARCHIVE_TIME)


@pytest.mark.parametrize(
    "field", ["scope", "generation", "configuration", "configured_time", "paused", "selector"]
)
def test_each_current_dataset_operand_independently_rejects_holder(field: str) -> None:
    dataset, claim = history_dataset(), _claim()
    if field == "scope":
        dataset = replace(dataset, scope=RepositoryScope(102, 202))
    elif field == "generation":
        dataset = replace(dataset, generation=2)
    elif field == "configuration":
        dataset = replace(dataset, configuration_revision=2)
    elif field == "configured_time":
        dataset = replace(dataset, configured_at=ARCHIVE_TIME + timedelta(microseconds=1))
    else:
        payload = dataset.configuration.model_dump()
        payload["enabled" if field == "paused" else "workflowIds"] = (
            False if field == "paused" else [405]
        )
        dataset = replace(
            dataset,
            state="paused" if field == "paused" else "active",
            configuration=HistoryConfiguration.model_validate(payload),
        )
    assert not current_history_recheck(dataset, claim.state, claim, ARCHIVE_TIME)


@pytest.mark.parametrize(
    "total,repairs,source,allowed",
    [
        (127, 127, "repair", True),
        (128, 128, "repair", False),
        (128, 128, "recent", True),
        (255, 0, "repair", True),
        (255, 128, "recent", True),
        (256, 128, "recent", False),
    ],
)
def test_separate_source_reserve_never_exceeds_total_bound(
    total: int, repairs: int, source: HistoryRecheckSource, allowed: bool
) -> None:
    assert recheck_capacity_available(total, repairs, source) is allowed


@pytest.mark.parametrize(
    "total,repairs", [(True, 0), (1, True), (-1, 0), (257, 0), (2, 3), (129, 129)]
)
def test_capacity_counts_must_be_admitted_not_clamped(total: int, repairs: int) -> None:
    with pytest.raises(ValueError):
        recheck_capacity_available(total, repairs, "recent")


@pytest.mark.parametrize("revision", [MAX_SAFE_JSON_INTEGER - 1, MAX_SAFE_JSON_INTEGER])
def test_revision_limit_blocks_new_work_without_rejecting_duplicate_hints(revision: int) -> None:
    state = replace(_initial(), revision=revision)
    assert merge_history_recheck_hint(state, state.hint) == state
    assert (
        acquire_history_recheck(
            history_dataset(), state, now=ARCHIVE_TIME, worker_id="a" * 64, token="b" * 64
        )
        is None
    )
    with pytest.raises(ValueError, match="revision reserve"):
        merge_history_recheck_hint(state, _hint(latest=5))


@pytest.mark.parametrize("jitter", [-1, True, 61])
def test_jitter_rejects_unbounded_or_coerced_values(jitter: int) -> None:
    claim = _claim()
    with pytest.raises(ValueError, match="jitter"):
        finish_history_recheck(
            history_dataset(),
            claim.state,
            claim,
            now=ARCHIVE_TIME,
            outcome="deferred",
            jitter_seconds=jitter,
        )
