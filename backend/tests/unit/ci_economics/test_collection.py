from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics import (
    CollectionClaim,
    CollectionClaimAcquired,
    CollectionClaimLost,
    CollectionPolicy,
    CollectionState,
    CollectionTerminalized,
    acquire_collection_claim,
    collection_claim_is_active,
    complete_collection_claim,
    defer_collection_claim,
    expire_collection_state,
    initial_collection_state,
    reject_collection_claim,
)
from ci_coordinator.ci_economics.collection import (
    CollectionFinalOutcome,
    CollectionTerminalReason,
    RetryableCollectionFailureReason,
)
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.kernel import hash_object

from .factories import ATTEMPT, CONTRACT, NOW, POLICY, SUBJECT

WORKER = "c" * 64
SOURCE = ReconciliationCollectionSource(SUBJECT, CONTRACT)
PROVIDER_SOURCE = ProviderRunCollectionSource(ATTEMPT, NOW, "2026-03-10", "e" * 64)


def test_claim_is_active_only_for_its_exact_unexpired_generation() -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    acquired = acquire_collection_claim(
        state,
        SOURCE,
        worker_id=WORKER,
        now=NOW,
        policy=POLICY,
    )
    assert isinstance(acquired, CollectionClaimAcquired)

    assert collection_claim_is_active(
        acquired.state,
        acquired.claim,
        acquired.claim.lease_expires_at - timedelta(microseconds=1),
    )
    assert not collection_claim_is_active(
        acquired.state,
        acquired.claim,
        acquired.claim.lease_expires_at,
    )


def test_expired_lease_is_reclaimed_with_a_new_revision_generation_and_token() -> None:
    first = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    second = _acquire(first.state, first.claim.lease_expires_at)

    assert second.claim.revision == first.claim.revision + 1
    assert second.claim.generation == first.claim.generation + 1
    assert second.claim.token != first.claim.token
    assert isinstance(
        complete_collection_claim(second.state, first.claim, second.claim.claimed_at),
        CollectionClaimLost,
    )


def test_transient_failure_advances_state_and_cannot_leave_a_poison_prefix() -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)

    deferred = defer_collection_claim(
        acquired.state,
        acquired.claim,
        NOW + timedelta(seconds=1),
        "provider_unavailable",
        POLICY,
    )

    assert not isinstance(deferred, CollectionClaimLost)
    assert deferred.status == "deferred"
    assert deferred.revision == acquired.state.revision + 1
    assert deferred.next_attempt_at is not None
    assert deferred.lease_token is None


def test_evidence_conflict_cannot_enter_the_retryable_failure_path() -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)

    with pytest.raises(ValueError, match="retryable collection failure reason"):
        defer_collection_claim(
            acquired.state,
            acquired.claim,
            NOW + timedelta(seconds=1),
            cast(RetryableCollectionFailureReason, "evidence_conflict"),
            POLICY,
        )


def test_state_rejects_evidence_conflict_outside_its_terminal_path() -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    deferred = defer_collection_claim(
        acquired.state,
        acquired.claim,
        NOW + timedelta(seconds=1),
        "provider_unavailable",
        POLICY,
    )
    assert not isinstance(deferred, CollectionClaimLost)

    with pytest.raises(ValueError, match="causal history"):
        replace(deferred, last_failure_reason="evidence_conflict")


def test_state_rejects_a_captured_outcome_without_a_claim() -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    captured = complete_collection_claim(acquired.state, acquired.claim, NOW)
    assert not isinstance(captured, CollectionClaimLost)

    with pytest.raises(ValueError, match="causal history"):
        replace(captured, attempt_count=0, claim_generation=0)


def test_state_rejects_premature_attempt_exhaustion() -> None:
    first = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    exhausted = replace(
        first.state,
        revision=POLICY.maximum_attempts,
        attempt_count=POLICY.maximum_attempts,
        claim_generation=POLICY.maximum_attempts,
    )
    terminal = acquire_collection_claim(
        exhausted,
        SOURCE,
        worker_id=WORKER,
        now=first.claim.lease_expires_at,
        policy=POLICY,
    )
    assert isinstance(terminal, CollectionTerminalized)

    with pytest.raises(ValueError, match="causal history"):
        replace(
            terminal.state,
            attempt_count=POLICY.maximum_attempts - 1,
            claim_generation=POLICY.maximum_attempts - 1,
        )


def test_collection_transition_rejects_a_substituted_policy() -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    substituted = replace(POLICY, lease_seconds=POLICY.lease_seconds + 1)

    with pytest.raises(ValueError, match="another policy"):
        acquire_collection_claim(
            state,
            SOURCE,
            worker_id=WORKER,
            now=NOW,
            policy=substituted,
        )


def test_transition_result_envelopes_reject_inconsistent_states() -> None:
    pending = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    acquired = _acquire(pending, NOW)

    with pytest.raises(ValueError, match="state and claim diverge"):
        CollectionClaimAcquired(pending, acquired.claim)
    with pytest.raises(ValueError, match="requires a terminal state"):
        CollectionTerminalized(pending)


@pytest.mark.parametrize("terminal", ["captured", "terminal_unavailable"])
def test_terminal_evidence_expires_once_without_becoming_pending_again(terminal: str) -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    if terminal == "captured":
        completed = complete_collection_claim(acquired.state, acquired.claim, NOW)
    else:
        completed = reject_collection_claim(acquired.state, acquired.claim, NOW)
    assert not isinstance(completed, CollectionClaimLost)

    expired = expire_collection_state(completed, completed.evidence_retain_until)

    assert expired is not None
    assert expired.status == "expired"
    assert expired.final_outcome == terminal
    assert (
        acquire_collection_claim(
            expired,
            SOURCE,
            worker_id=WORKER,
            now=expired.evidence_retain_until,
            policy=POLICY,
        )
        is None
    )


def test_claim_terminalizes_before_a_lease_would_cross_the_source_deadline() -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    outcome = acquire_collection_claim(
        state,
        SOURCE,
        worker_id=WORKER,
        now=state.deadline_at - timedelta(seconds=POLICY.lease_seconds),
        policy=POLICY,
    )

    assert isinstance(outcome, CollectionTerminalized)
    assert outcome.state.terminal_reason == "deadline_exceeded"


def test_collection_can_truthfully_terminalize_after_evidence_retention() -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    completed_at = state.evidence_retain_until + timedelta(seconds=1)

    outcome = acquire_collection_claim(
        state,
        SOURCE,
        worker_id=WORKER,
        now=completed_at,
        policy=POLICY,
    )

    assert isinstance(outcome, CollectionTerminalized)
    assert outcome.state.completed_at == completed_at
    assert outcome.state.terminal_reason == "deadline_exceeded"
    expired = expire_collection_state(outcome.state, completed_at)
    assert expired is not None
    assert expired.status == "expired"


def test_registration_time_cannot_extend_the_source_owned_horizon() -> None:
    with pytest.raises(ValueError, match="outside its eligibility window"):
        initial_collection_state(
            SUBJECT.subject_id,
            NOW,
            NOW + timedelta(seconds=POLICY.collection_window_seconds),
            POLICY,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"initial_backoff_seconds": POLICY.maximum_backoff_seconds + 1},
            "initial collection backoff cannot exceed",
        ),
        (
            {"maximum_backoff_seconds": POLICY.collection_window_seconds},
            "maximum collection backoff must be shorter",
        ),
        (
            {"lease_seconds": POLICY.collection_window_seconds},
            "collection lease must be shorter",
        ),
        (
            {"collection_window_seconds": 2_592_000},
            "evidence retention must outlive",
        ),
    ],
)
def test_collection_policy_rejects_incoherent_time_budgets(
    changes: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(POLICY, **changes)


def test_collection_state_rejects_non_causal_counters_and_times() -> None:
    pending = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    acquired = _acquire(pending, NOW)

    invalid_states = (
        (replace, {"deadline_at": NOW}, "strictly ordered"),
        (
            replace,
            {"attempt_count": 4, "claim_generation": 4, "revision": 4},
            "attempt count exceeds",
        ),
        (replace, {"backoff_seconds": 81}, "backoff exceeds"),
        (replace, {"claim_generation": 1, "revision": 1}, "must equal attempt count"),
        (replace, {"lease_owner_id": WORKER}, "lease fields must be all present"),
        (replace, {"next_attempt_at": pending.deadline_at}, "outside the collection window"),
        (
            replace,
            {"completed_at": pending.source_created_at - timedelta(microseconds=1)},
            "completion precedes",
        ),
    )
    for constructor, changes, message in invalid_states:
        with pytest.raises(ValueError, match=message):
            constructor(pending, **changes)

    with pytest.raises(ValueError, match="cannot precede its claim generation"):
        replace(acquired.state, revision=0)
    with pytest.raises(ValueError, match="final outcome is invalid"):
        replace(pending, final_outcome=cast(CollectionFinalOutcome, "unknown"))


@pytest.mark.parametrize("transition", ["complete", "defer", "reject"])
def test_expired_claim_cannot_commit_any_holder_transition(transition: str) -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    at_expiry = acquired.claim.lease_expires_at

    if transition == "complete":
        result = complete_collection_claim(acquired.state, acquired.claim, at_expiry)
    elif transition == "defer":
        result = defer_collection_claim(
            acquired.state,
            acquired.claim,
            at_expiry,
            "provider_unavailable",
            POLICY,
        )
    else:
        result = reject_collection_claim(acquired.state, acquired.claim, at_expiry)

    assert isinstance(result, CollectionClaimLost)


@pytest.mark.parametrize(
    ("policy", "expected_reason"),
    [
        (replace(POLICY, maximum_attempts=1), "attempts_exhausted"),
        (
            CollectionPolicy(
                collection_window_seconds=60,
                initial_backoff_seconds=10,
                maximum_backoff_seconds=20,
                maximum_attempts=3,
                lease_seconds=30,
                evidence_days=1,
                tombstone_grace_seconds=300,
            ),
            "deadline_exceeded",
        ),
    ],
)
def test_deferral_terminalizes_when_no_bounded_retry_can_complete(
    policy: CollectionPolicy,
    expected_reason: CollectionTerminalReason,
) -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, policy)
    acquired = acquire_collection_claim(
        state,
        SOURCE,
        worker_id=WORKER,
        now=NOW,
        policy=policy,
    )
    assert isinstance(acquired, CollectionClaimAcquired)
    checked_at = min(
        acquired.claim.lease_expires_at - timedelta(microseconds=1),
        state.deadline_at - timedelta(seconds=policy.lease_seconds),
    )

    result = defer_collection_claim(
        acquired.state,
        acquired.claim,
        checked_at,
        "provider_unavailable",
        policy,
    )

    assert not isinstance(result, CollectionClaimLost)
    assert result.status == "terminal_unavailable"
    assert result.terminal_reason == expected_reason


def test_expiry_requires_terminal_evidence_and_the_retention_boundary() -> None:
    pending = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    acquired = _acquire(pending, NOW)
    captured = complete_collection_claim(acquired.state, acquired.claim, NOW)
    assert not isinstance(captured, CollectionClaimLost)

    assert expire_collection_state(pending, pending.evidence_retain_until) is None
    assert (
        expire_collection_state(
            captured,
            captured.evidence_retain_until - timedelta(microseconds=1),
        )
        is None
    )


def test_claim_activity_rejects_non_exact_state_or_claim() -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)

    assert not collection_claim_is_active(
        cast(CollectionState, object()),
        acquired.claim,
        NOW,
    )
    assert not collection_claim_is_active(
        acquired.state,
        cast(CollectionClaim, object()),
        NOW,
    )


@pytest.mark.parametrize(
    ("revision", "terminal_reason"),
    [
        (1, None),
        (0, "deadline_exceeded"),
    ],
)
def test_pending_state_rejects_stale_transition_fields(
    revision: int,
    terminal_reason: CollectionTerminalReason | None,
) -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)

    with pytest.raises(ValueError, match="do not match its status"):
        replace(state, revision=revision, terminal_reason=terminal_reason)


@pytest.mark.parametrize(
    ("final_outcome", "terminal_reason"),
    [
        ("captured", "deadline_exceeded"),
        ("terminal_unavailable", None),
    ],
)
def test_expired_state_preserves_terminal_outcome_shape(
    final_outcome: CollectionFinalOutcome,
    terminal_reason: CollectionTerminalReason | None,
) -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    completed = complete_collection_claim(acquired.state, acquired.claim, NOW)
    assert not isinstance(completed, CollectionClaimLost)
    expired = expire_collection_state(completed, completed.evidence_retain_until)
    assert expired is not None

    with pytest.raises(ValueError, match="do not match its status"):
        replace(expired, final_outcome=final_outcome, terminal_reason=terminal_reason)


def test_attempt_bound_terminalizes_an_expired_final_lease() -> None:
    first = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    exhausted = replace(
        first.state,
        revision=POLICY.maximum_attempts,
        attempt_count=POLICY.maximum_attempts,
        claim_generation=POLICY.maximum_attempts,
    )

    outcome = acquire_collection_claim(
        exhausted,
        SOURCE,
        worker_id=WORKER,
        now=first.claim.lease_expires_at,
        policy=POLICY,
    )

    assert isinstance(outcome, CollectionTerminalized)
    assert outcome.state.terminal_reason == "attempts_exhausted"


@pytest.mark.parametrize(
    "source", [SOURCE, PROVIDER_SOURCE], ids=["reconciliation", "provider-run"]
)
def test_both_sources_share_the_existing_fenced_retention_lifecycle(
    source: CollectionSource,
) -> None:
    pending = initial_collection_state(source.source_id, NOW, NOW, POLICY)
    first = _acquire(pending, NOW, source)
    second = _acquire(first.state, first.claim.lease_expires_at, source)

    assert first.claim.source == second.claim.source == source
    assert isinstance(
        complete_collection_claim(second.state, first.claim, second.claim.claimed_at),
        CollectionClaimLost,
    )
    captured = complete_collection_claim(second.state, second.claim, second.claim.claimed_at)
    assert isinstance(captured, CollectionState)
    assert captured.status == "captured"
    assert captured.source_created_at == pending.source_created_at
    assert captured.evidence_retain_until == pending.evidence_retain_until
    assert captured.tombstone_retain_until == pending.tombstone_retain_until
    expired = expire_collection_state(captured, captured.evidence_retain_until)
    assert isinstance(expired, CollectionState)
    assert expired.status == "expired"
    assert (
        acquire_collection_claim(
            expired, source, worker_id=WORKER, now=expired.evidence_retain_until, policy=POLICY
        )
        is None
    )


def test_legacy_claim_and_retry_keep_their_original_hash_inputs() -> None:
    acquired = _acquire(initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY), NOW)
    assert acquired.claim.token == hash_object(
        {
            "schemaVersion": "ci-economics-collection-claim/v1",
            "subjectId": SUBJECT.subject_id,
            "contractHash": CONTRACT.contract_hash,
            "policyHash": POLICY.policy_hash,
            "workerId": WORKER,
            "generation": 1,
            "revision": 1,
            "claimedAt": NOW.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
    )
    deferred_at = NOW + timedelta(seconds=1)
    deferred = defer_collection_claim(
        acquired.state, acquired.claim, deferred_at, "provider_unavailable", POLICY
    )
    assert isinstance(deferred, CollectionState)
    minimum = (POLICY.initial_backoff_seconds + 1) // 2
    width = POLICY.initial_backoff_seconds - minimum + 1
    jitter_digest = hash_object(
        {
            "schemaVersion": "ci-economics-collection-retry-jitter/v1",
            "subjectId": SUBJECT.subject_id,
            "attemptCount": 1,
        }
    )
    assert deferred.next_attempt_at == deferred_at + timedelta(
        seconds=minimum + int(jitter_digest[:16], 16) % width
    )


@pytest.mark.parametrize(
    "source", [SOURCE, PROVIDER_SOURCE], ids=["reconciliation", "provider-run"]
)
def test_source_identity_is_checked_before_claim_acquisition(source: CollectionSource) -> None:
    state = initial_collection_state("f" * 64, NOW, NOW, POLICY)
    with pytest.raises(ValueError, match="another source"):
        acquire_collection_claim(state, source, worker_id=WORKER, now=NOW, policy=POLICY)


@pytest.mark.parametrize("offset", [-1, 1])
def test_provider_source_cannot_reanchor_an_existing_collection(offset: int) -> None:
    state = initial_collection_state(PROVIDER_SOURCE.source_id, NOW, NOW, POLICY)
    changed = replace(PROVIDER_SOURCE, run_created_at=NOW + timedelta(microseconds=offset))
    assert changed.source_id == PROVIDER_SOURCE.source_id
    with pytest.raises(ValueError, match="creation time changed"):
        acquire_collection_claim(state, changed, worker_id=WORKER, now=NOW, policy=POLICY)


@pytest.mark.parametrize(
    "changed",
    [
        replace(PROVIDER_SOURCE, provider_api_version="2026-03-11"),
        replace(PROVIDER_SOURCE, source_evidence_digest="f" * 64),
    ],
    ids=["api-version", "evidence-digest"],
)
def test_provider_claim_token_binds_nonidentity_provenance(
    changed: ProviderRunCollectionSource,
) -> None:
    state = initial_collection_state(PROVIDER_SOURCE.source_id, NOW, NOW, POLICY)
    original = _acquire(state, NOW, PROVIDER_SOURCE)
    alternative = _acquire(state, NOW, changed)
    assert original.claim.source.source_id == alternative.claim.source.source_id
    assert original.claim.token != alternative.claim.token
    assert not collection_claim_is_active(original.state, alternative.claim, NOW)


def _acquire(
    state: CollectionState, now: datetime, source: CollectionSource = SOURCE
) -> CollectionClaimAcquired:
    outcome = acquire_collection_claim(
        state,
        source,
        worker_id=WORKER,
        now=now,
        policy=POLICY,
    )
    assert isinstance(outcome, CollectionClaimAcquired)
    return outcome
