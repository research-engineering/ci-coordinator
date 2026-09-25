from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.reconciliation import (
    MAX_RECONCILIATION_DEADLINE_SECONDS,
    ConvergenceTerminalReason,
    ReconciliationClaimLost,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationConvergenceState,
    ReconciliationSnapshot,
    ReconciliationSubject,
    ReconciliationTerminalRequired,
    SignalObservation,
    acquire_reconciliation_claim,
    convergence_failure,
    defer_reconciliation_claim,
    initial_convergence_state,
    release_terminal_claim,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
WORKER = "1" * 64
SUBJECT = ReconciliationSubject.create(
    installation_id=100,
    repository_id=200,
    event_name="push",
    ref="refs/heads/main",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=300,
    run_attempt=1,
)
CONTRACT = ReconciliationContract(
    (
        ProviderSignal.derive(
            execution_profile_id="python-313",
            shard_id="ci_shard_0123456789abcdef0123456789abcdef",
        ),
    ),
    (),
)
POLICY = ReconciliationConvergencePolicy(
    deadline_seconds=100,
    initial_backoff_seconds=5,
    max_backoff_seconds=20,
    max_attempts=3,
    poll_timeout_seconds=10,
    lease_seconds=15,
)
EMPTY_SNAPSHOT = ReconciliationSnapshot(SUBJECT, CONTRACT, 0, ())


@pytest.mark.parametrize(
    "changes",
    [
        {"deadline_seconds": 0},
        {"initial_backoff_seconds": 21},
        {"max_backoff_seconds": 101},
        {"max_attempts": 0},
        {"poll_timeout_seconds": 15},
        {"lease_seconds": 101},
    ],
)
def test_policy_rejects_unbounded_or_incoherent_lifetimes(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        replace(POLICY, **changes)


def test_state_rejects_a_deadline_beyond_the_domain_bound() -> None:
    initial = initial_convergence_state(NOW, POLICY)

    with pytest.raises(ValueError, match="deadline duration exceeds"):
        replace(
            initial,
            deadline_at=NOW + timedelta(seconds=MAX_RECONCILIATION_DEADLINE_SECONDS + 1),
        )


def test_claim_rejects_a_pollable_attempt_at_the_deadline() -> None:
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )
    assert acquired is not None

    with pytest.raises(ValueError, match="cannot start at its deadline"):
        replace(
            acquired.claim,
            claimed_at=acquired.claim.deadline_at,
            lease_expires_at=acquired.claim.deadline_at + timedelta(seconds=POLICY.lease_seconds),
        )


def test_claim_is_due_once_and_reclaim_uses_a_new_fencing_generation() -> None:
    initial = initial_convergence_state(NOW, POLICY)

    first = acquire_reconciliation_claim(
        initial,
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )

    assert first is not None
    assert first.claim.attempt_count == 1
    assert first.claim.may_poll
    assert first.state.lease_acquired_at == NOW
    assert first.state.lease_expires_at == NOW + timedelta(seconds=POLICY.lease_seconds)
    assert (
        acquire_reconciliation_claim(
            first.state,
            SUBJECT,
            CONTRACT,
            0,
            worker_id="2" * 64,
            now=NOW + timedelta(seconds=14),
            policy=POLICY,
        )
        is None
    )

    reclaimed = acquire_reconciliation_claim(
        first.state,
        SUBJECT,
        CONTRACT,
        0,
        worker_id="2" * 64,
        now=NOW + timedelta(seconds=15),
        policy=POLICY,
    )

    assert reclaimed is not None
    assert reclaimed.claim.generation == 2
    assert reclaimed.claim.attempt_count == 2
    assert reclaimed.claim.token != first.claim.token


def test_defer_advances_bounded_backoff_and_rejects_a_stale_token() -> None:
    first = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )
    assert first is not None

    deferred = defer_reconciliation_claim(
        first.state,
        first.claim,
        NOW + timedelta(seconds=1),
        snapshot_revision=EMPTY_SNAPSHOT.revision,
    )

    assert isinstance(deferred, ReconciliationConvergenceState)
    delay = int((deferred.next_attempt_at - (NOW + timedelta(seconds=1))).total_seconds())
    assert (POLICY.initial_backoff_seconds + 1) // 2 <= delay <= POLICY.initial_backoff_seconds
    assert deferred.backoff_seconds == 10
    assert deferred.lease_token is None
    assert deferred.lease_acquired_at is None
    assert isinstance(
        defer_reconciliation_claim(
            first.state,
            first.claim,
            NOW + timedelta(seconds=15),
            snapshot_revision=EMPTY_SNAPSHOT.revision,
        ),
        ReconciliationClaimLost,
    )


def test_retry_jitter_is_bounded_and_subject_stable() -> None:
    first = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )
    assert first is not None

    outcomes = tuple(
        defer_reconciliation_claim(
            first.state, first.claim, NOW, snapshot_revision=EMPTY_SNAPSHOT.revision
        )
        for _ in range(2)
    )

    assert all(isinstance(outcome, ReconciliationConvergenceState) for outcome in outcomes)
    admitted = [
        outcome for outcome in outcomes if isinstance(outcome, ReconciliationConvergenceState)
    ]
    assert admitted[0].next_attempt_at == admitted[1].next_attempt_at
    delay = int((admitted[0].next_attempt_at - NOW).total_seconds())
    assert (POLICY.initial_backoff_seconds + 1) // 2 <= delay <= POLICY.initial_backoff_seconds


@pytest.mark.parametrize("offset_us", [-1, 0, 1])
def test_defer_deadline_keeps_claim_and_binds_the_observed_snapshot(offset_us: int) -> None:
    initial = initial_convergence_state(NOW, POLICY)
    acquired = acquire_reconciliation_claim(
        initial,
        SUBJECT,
        CONTRACT,
        EMPTY_SNAPSHOT.revision,
        worker_id=WORKER,
        now=initial.deadline_at - timedelta(seconds=1),
        policy=POLICY,
    )
    assert acquired is not None
    observed = SignalObservation(
        "progress",
        SUBJECT.subject_id,
        CONTRACT.provider_signals[0].signal_id,
        SUBJECT.workflow_run_id,
        SUBJECT.run_attempt,
        1,
        "in_progress",
        None,
    )
    snapshot = ReconciliationSnapshot(SUBJECT, CONTRACT, 1, (observed,))
    at = initial.deadline_at + timedelta(microseconds=offset_us)

    outcome = defer_reconciliation_claim(
        acquired.state, acquired.claim, at, snapshot_revision=snapshot.revision
    )

    if offset_us < 0:
        assert isinstance(outcome, ReconciliationConvergenceState)
        assert outcome.next_attempt_at == initial.deadline_at
        assert outcome.backoff_seconds == POLICY.initial_backoff_seconds * 2
        assert outcome.lease_token is None
    else:
        assert isinstance(outcome, ReconciliationTerminalRequired)
        assert outcome.claim is acquired.claim
        assert outcome.claim.revision == 0
        assert outcome.snapshot_revision == snapshot.revision == 1
        assert outcome.reason == "deadline_exceeded"
        assert acquired.state.lease_token == acquired.claim.token
        assert acquired.state.backoff_seconds == POLICY.initial_backoff_seconds


def test_terminal_requirement_does_not_replace_stale_claim_or_attempt_only_errors() -> None:
    initial = initial_convergence_state(NOW, POLICY)
    acquired = acquire_reconciliation_claim(
        replace(initial, attempt_count=POLICY.max_attempts - 1, claim_generation=2),
        SUBJECT,
        CONTRACT,
        EMPTY_SNAPSHOT.revision,
        worker_id=WORKER,
        now=initial.deadline_at - timedelta(seconds=1),
        policy=POLICY,
    )
    assert acquired is not None
    assert acquired.claim.attempt_count == POLICY.max_attempts
    with pytest.raises(ValueError, match="terminal reconciliation claim cannot be deferred"):
        defer_reconciliation_claim(
            acquired.state,
            acquired.claim,
            acquired.claim.claimed_at,
            snapshot_revision=EMPTY_SNAPSHOT.revision,
        )
    required = defer_reconciliation_claim(
        acquired.state,
        acquired.claim,
        initial.deadline_at,
        snapshot_revision=EMPTY_SNAPSHOT.revision,
    )
    assert isinstance(required, ReconciliationTerminalRequired)
    for stale_state in (
        replace(acquired.state, lease_token="2" * 64),
        replace(acquired.state, claim_generation=acquired.state.claim_generation + 1),
    ):
        assert isinstance(
            defer_reconciliation_claim(
                stale_state,
                acquired.claim,
                initial.deadline_at,
                snapshot_revision=EMPTY_SNAPSHOT.revision,
            ),
            ReconciliationClaimLost,
        )
    assert isinstance(
        defer_reconciliation_claim(
            acquired.state,
            acquired.claim,
            acquired.claim.lease_expires_at,
            snapshot_revision=EMPTY_SNAPSHOT.revision,
        ),
        ReconciliationClaimLost,
    )


@pytest.mark.parametrize("revision", [-1, True, 9_007_199_254_740_992])
def test_terminal_requirement_rejects_invalid_snapshot_revision(revision: int) -> None:
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        EMPTY_SNAPSHOT.revision,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )
    assert acquired is not None
    with pytest.raises(ValueError, match="snapshot revision"):
        ReconciliationTerminalRequired(acquired.claim, revision)
    with pytest.raises(ValueError, match="snapshot revision"):
        defer_reconciliation_claim(acquired.state, acquired.claim, NOW, snapshot_revision=revision)


@pytest.mark.parametrize(
    ("state_change", "at", "reason", "kind"),
    [
        ({}, NOW + timedelta(seconds=100), "deadline_exceeded", "reconciliation_timed_out"),
        (
            {"attempt_count": 3, "claim_generation": 3},
            NOW,
            "attempts_exhausted",
            "reconciliation_attempts_exhausted",
        ),
    ],
)
def test_exhausted_lifecycle_claims_terminal_failure_without_provider_poll(
    state_change: dict[str, int],
    at: datetime,
    reason: ConvergenceTerminalReason,
    kind: str,
) -> None:
    state = initial_convergence_state(NOW, POLICY)
    if state_change:
        state = replace(state, attempt_count=3, claim_generation=3)
    claim = acquire_reconciliation_claim(
        state,
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=at,
        policy=POLICY,
    )

    assert claim is not None
    assert claim.claim.terminal_reason == reason
    assert not claim.claim.may_poll
    result = convergence_failure(claim.claim, reason)
    assert result.state == "failure"
    assert result.findings[0].kind == kind


def test_terminal_timeout_claim_remains_valid_long_after_subject_deadline() -> None:
    claimed_at = NOW + timedelta(days=2)

    acquired = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=claimed_at,
        policy=POLICY,
    )

    assert acquired is not None
    assert acquired.claim.terminal_reason == "deadline_exceeded"
    assert acquired.state.lease_acquired_at == claimed_at
    assert acquired.state.lease_expires_at == claimed_at + timedelta(seconds=POLICY.lease_seconds)


def test_terminal_release_requires_the_current_unexpired_fencing_token() -> None:
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )
    assert acquired is not None

    released = release_terminal_claim(acquired.state, acquired.claim, NOW + timedelta(seconds=1))

    assert not isinstance(released, ReconciliationClaimLost)
    assert released.lease_token is None
    assert released.lease_acquired_at is None
    assert isinstance(
        release_terminal_claim(acquired.state, acquired.claim, NOW - timedelta(microseconds=1)),
        ReconciliationClaimLost,
    )
    assert isinstance(
        release_terminal_claim(acquired.state, acquired.claim, NOW + timedelta(seconds=15)),
        ReconciliationClaimLost,
    )
