from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Never

import pytest

from ci_coordinator.app.reconciliation_round import ReconciliationRoundService
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.kernel import FixedClock
from ci_coordinator.reconciliation import (
    ObservationAppend,
    ProviderSignalAmbiguity,
    ReconciliationAttemptClaim,
    ReconciliationClaimAcquired,
    ReconciliationClaimLost,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationConvergenceState,
    ReconciliationPoller,
    ReconciliationPollOutcome,
    ReconciliationPollUnavailable,
    ReconciliationResult,
    ReconciliationSnapshot,
    ReconciliationSubject,
    ReconciliationTerminalRequired,
    ResultRecord,
    SignalConclusion,
    SignalObservation,
    SignalStatus,
    SubjectRegistration,
    acquire_reconciliation_claim,
    append_observation,
    defer_reconciliation_claim,
    initial_convergence_state,
    record_result,
)
from ci_coordinator.shadow_mode import ShadowEvidenceRecord

NOW = datetime(2026, 7, 17, 10, tzinfo=UTC)
WORKER_ID = "a" * 64
POLICY = ReconciliationConvergencePolicy(
    deadline_seconds=30,
    initial_backoff_seconds=1,
    max_backoff_seconds=4,
    max_attempts=2,
    poll_timeout_seconds=1,
    lease_seconds=5,
)
SUBJECT = ReconciliationSubject.create(
    installation_id=100,
    repository_id=200,
    event_name="push",
    ref="refs/heads/main",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=7001,
    run_attempt=1,
)
SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
CONTRACT = ReconciliationContract((SIGNAL,), ())


class _Source:
    def __init__(self, claims: tuple[ReconciliationAttemptClaim, ...]) -> None:
        self._claims = list(claims)
        self.calls = 0

    async def claim_next(self) -> ReconciliationAttemptClaim | None:
        self.calls += 1
        return self._claims.pop(0) if self._claims else None


class _Persistence:
    def __init__(self) -> None:
        self.snapshot = ReconciliationSnapshot(SUBJECT, CONTRACT, 0, ())
        self.result: ReconciliationResult | None = None
        self.defer_calls: list[ReconciliationAttemptClaim] = []
        self.claim_lost = False

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
    ) -> SubjectRegistration:
        del subject, contract
        raise AssertionError("not used")

    async def load_snapshot(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationSnapshot | ReconciliationClaimLost:
        assert claim.subject == SUBJECT
        return ReconciliationClaimLost(SUBJECT.subject_id) if self.claim_lost else self.snapshot

    async def append_observation(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        observation: SignalObservation,
    ) -> ObservationAppend | ReconciliationClaimLost:
        assert claim.subject == SUBJECT
        if self.claim_lost:
            return ReconciliationClaimLost(SUBJECT.subject_id)
        outcome = append_observation(self.snapshot, expected_revision, observation)
        if hasattr(outcome, "snapshot"):
            self.snapshot = outcome.snapshot
        return outcome

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> ResultRecord | ReconciliationClaimLost:
        assert claim.subject == SUBJECT
        if self.claim_lost:
            return ReconciliationClaimLost(SUBJECT.subject_id)
        outcome = record_result(self.snapshot, expected_revision, result, self.result)
        if hasattr(outcome, "result"):
            self.result = outcome.result
        return outcome

    async def defer_claim(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationConvergenceState | ReconciliationClaimLost | ReconciliationTerminalRequired:
        self.defer_calls.append(claim)
        if self.claim_lost:
            return ReconciliationClaimLost(SUBJECT.subject_id)
        return initial_convergence_state(NOW, POLICY)


class _DeadlinePersistence(_Persistence):
    def __init__(
        self, after_requirement: Literal["none", "revision", "lost", "cancel", "foreign"] = "none"
    ) -> None:
        super().__init__()
        self.after_requirement = after_requirement
        self.recorded_revisions: list[int] = []

    async def defer_claim(
        self, claim: ReconciliationAttemptClaim
    ) -> ReconciliationTerminalRequired:
        self.defer_calls.append(claim)
        outcome = ReconciliationTerminalRequired(claim, self.snapshot.revision)
        if self.after_requirement == "revision":
            await self.append_observation(claim, self.snapshot.revision, _observation()[0])
        elif self.after_requirement == "lost":
            self.claim_lost = True
        elif self.after_requirement == "cancel":
            raise asyncio.CancelledError
        elif self.after_requirement == "foreign":
            return replace(outcome, claim=replace(claim, token="b" * 64))
        return outcome

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> ResultRecord | ReconciliationClaimLost:
        self.recorded_revisions.append(expected_revision)
        return await super().record_result(claim, expected_revision, result)


class _Poller:
    def __init__(
        self,
        outcome: ReconciliationPollOutcome | BaseException,
    ) -> None:
        self._outcome = outcome
        self.calls = 0

    async def poll(
        self,
        subject: ReconciliationSubject,
        provider_signals: tuple[ProviderSignal, ...],
    ) -> ReconciliationPollOutcome:
        self.calls += 1
        assert subject == SUBJECT
        assert provider_signals == (SIGNAL,)
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class _BlockingPoller:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def poll(
        self,
        subject: ReconciliationSubject,
        provider_signals: tuple[ProviderSignal, ...],
    ) -> Never:
        del subject, provider_signals
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _TerminalPersistence:
    def __init__(self, persistence: _Persistence) -> None:
        self._persistence = persistence
        self.calls: list[tuple[ReconciliationAttemptClaim, int, ReconciliationResult]] = []

    async def record_result_with_evidence(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
        evidence: tuple[ShadowEvidenceRecord, ...],
    ) -> ResultRecord | ReconciliationClaimLost:
        assert evidence == ()
        self.calls.append((claim, expected_revision, result))
        return await self._persistence.record_result(claim, expected_revision, result)


class _NoEvidenceProjector:
    def project(
        self,
        claim: ReconciliationAttemptClaim,
        result: ReconciliationResult,
    ) -> tuple[ShadowEvidenceRecord, ...]:
        del claim, result
        return ()


def _claim(*, attempt: int = 1, at: datetime = NOW) -> ReconciliationAttemptClaim:
    snapshot = ReconciliationSnapshot(SUBJECT, CONTRACT, 0, ())
    state = initial_convergence_state(NOW, POLICY)
    acquired: ReconciliationClaimAcquired | None = None
    for index in range(attempt):
        acquired = acquire_reconciliation_claim(
            state,
            SUBJECT,
            CONTRACT,
            snapshot.revision,
            worker_id=WORKER_ID,
            now=at + timedelta(seconds=index),
            policy=POLICY,
        )
        assert isinstance(acquired, ReconciliationClaimAcquired)
        if index + 1 < attempt:
            deferred = defer_reconciliation_claim(
                acquired.state,
                acquired.claim,
                at + timedelta(seconds=index),
                snapshot_revision=snapshot.revision,
            )
            assert isinstance(deferred, ReconciliationConvergenceState)
            state = deferred
    assert acquired is not None
    return acquired.claim


def _terminal_deadline_claim() -> ReconciliationAttemptClaim:
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(NOW, POLICY),
        SUBJECT,
        CONTRACT,
        0,
        worker_id=WORKER_ID,
        now=NOW + timedelta(seconds=POLICY.deadline_seconds),
        policy=POLICY,
    )
    assert isinstance(acquired, ReconciliationClaimAcquired)
    return acquired.claim


def _observation(
    status: SignalStatus = "completed",
    conclusion: SignalConclusion | None = "success",
) -> tuple[SignalObservation, ...]:
    return (
        SignalObservation(
            "github-job-1:" + status + ":" + str(conclusion),
            SUBJECT.subject_id,
            SIGNAL.signal_id,
            SUBJECT.workflow_run_id,
            SUBJECT.run_attempt,
            1,
            status,
            conclusion,
        ),
    )


def _service(
    claim: ReconciliationAttemptClaim,
    persistence: _Persistence,
    poller: ReconciliationPoller,
    *,
    now: datetime = NOW,
    terminal: _TerminalPersistence | None = None,
) -> ReconciliationRoundService:
    return ReconciliationRoundService(
        source=_Source((claim,)),
        persistence=persistence,
        poller=poller,
        clock=FixedClock(now),
        convergence_policy=POLICY,
        max_claims=2,
        terminal_persistence=terminal,
        shadow_projector=_NoEvidenceProjector() if terminal is not None else None,
    )


def test_round_records_a_unique_successful_provider_signal() -> None:
    persistence = _Persistence()
    service = _service(_claim(), persistence, _Poller(_observation()))

    asyncio.run(service(asyncio.Event()))

    assert persistence.snapshot.revision == 1
    assert persistence.result is not None
    assert persistence.result.state == "success"
    assert persistence.defer_calls == []


def test_last_permitted_attempt_can_still_succeed() -> None:
    persistence = _Persistence()
    service = _service(_claim(attempt=POLICY.max_attempts), persistence, _Poller(_observation()))

    asyncio.run(service(asyncio.Event()))

    assert persistence.result is not None
    assert persistence.result.state == "success"


def test_pending_result_is_deferred_instead_of_persisted() -> None:
    persistence = _Persistence()
    service = _service(
        _claim(),
        persistence,
        _Poller(_observation("in_progress", None)),
    )

    asyncio.run(service(asyncio.Event()))

    assert persistence.result is None
    assert len(persistence.defer_calls) == 1


@pytest.mark.parametrize("poll_path", ["pending", "unavailable"])
@pytest.mark.parametrize("with_evidence_port", [False, True])
def test_locked_deadline_requirement_uses_actual_revision_on_both_defer_paths(
    poll_path: Literal["pending", "unavailable"], with_evidence_port: bool
) -> None:
    acquired = _claim()
    persistence = _DeadlinePersistence()
    progress = _observation("in_progress", None)
    outcome: ReconciliationPollOutcome | BaseException = progress
    if poll_path == "unavailable":
        persistence.snapshot = ReconciliationSnapshot(SUBJECT, CONTRACT, 1, progress)
        outcome = ReconciliationPollUnavailable("provider")
    terminal = _TerminalPersistence(persistence) if with_evidence_port else None
    assert acquired.deadline_at > NOW

    asyncio.run(
        _service(acquired, persistence, _Poller(outcome), terminal=terminal)(asyncio.Event())
    )

    assert acquired.revision == 0
    assert persistence.snapshot.revision == 1
    assert persistence.defer_calls == [acquired]
    assert persistence.recorded_revisions == [1]
    assert persistence.result is not None
    assert persistence.result.state == "failure"
    assert persistence.result.findings[0].kind == "reconciliation_timed_out"
    if terminal is not None:
        assert terminal.calls == [(acquired, 1, persistence.result)]


@pytest.mark.parametrize("change", ["revision", "lost"])
def test_terminal_requirement_cannot_promote_a_changed_snapshot_or_lost_claim(
    change: Literal["revision", "lost"],
) -> None:
    persistence = _DeadlinePersistence(change)
    terminal = _TerminalPersistence(persistence)

    asyncio.run(
        _service(
            _claim(),
            persistence,
            _Poller(_observation("in_progress", None)),
            terminal=terminal,
        )(asyncio.Event())
    )

    assert persistence.recorded_revisions == [1]
    assert persistence.result is None
    assert len(terminal.calls) == 1
    if change == "revision":
        assert persistence.snapshot.revision == 2


@pytest.mark.parametrize("change", ["cancel", "foreign"])
def test_invalid_or_cancelled_requirement_never_reaches_terminal_writer(
    change: Literal["cancel", "foreign"],
) -> None:
    persistence = _DeadlinePersistence(change)
    terminal = _TerminalPersistence(persistence)
    error = asyncio.CancelledError if change == "cancel" else ValueError

    with pytest.raises(error):
        asyncio.run(
            _service(
                _claim(),
                persistence,
                _Poller(_observation("in_progress", None)),
                terminal=terminal,
            )(asyncio.Event())
        )

    assert persistence.recorded_revisions == []
    assert persistence.result is None
    assert terminal.calls == []


def test_pending_last_attempt_becomes_terminal_failure() -> None:
    persistence = _Persistence()
    service = _service(
        _claim(attempt=POLICY.max_attempts),
        persistence,
        _Poller(()),
    )

    asyncio.run(service(asyncio.Event()))

    assert persistence.result is not None
    assert persistence.result.state == "failure"
    assert persistence.result.findings[0].kind == "reconciliation_attempts_exhausted"


def test_deadline_claim_is_terminal_without_provider_io() -> None:
    persistence = _Persistence()
    poller = _Poller(())
    service = _service(_terminal_deadline_claim(), persistence, poller)

    asyncio.run(service(asyncio.Event()))

    assert poller.calls == 0
    assert persistence.result is not None
    assert persistence.result.findings[0].kind == "reconciliation_timed_out"


def test_deadline_dominates_a_success_observed_by_an_active_claim() -> None:
    persistence = _Persistence()
    claimed_at = NOW + timedelta(seconds=POLICY.deadline_seconds - 1)
    service = _service(
        _claim(at=claimed_at),
        persistence,
        _Poller(_observation()),
        now=NOW + timedelta(seconds=POLICY.deadline_seconds),
    )

    asyncio.run(service(asyncio.Event()))

    assert persistence.snapshot.revision == 1
    assert persistence.result is not None
    assert persistence.result.state == "failure"
    assert persistence.result.findings[0].kind == "reconciliation_timed_out"


def test_provider_failure_retries_until_the_attempt_bound() -> None:
    first = _Persistence()
    asyncio.run(
        _service(
            _claim(),
            first,
            _Poller(ReconciliationPollUnavailable("provider")),
        )(asyncio.Event())
    )
    assert len(first.defer_calls) == 1
    assert first.result is None

    final = _Persistence()
    asyncio.run(
        _service(
            _claim(attempt=POLICY.max_attempts),
            final,
            _Poller(ReconciliationPollUnavailable("provider")),
        )(asyncio.Event())
    )
    assert final.result is not None
    assert final.result.findings[0].kind == "reconciliation_attempts_exhausted"


def test_unexpected_poller_defect_fails_the_round_without_retry() -> None:
    persistence = _Persistence()
    service = _service(_claim(), persistence, _Poller(RuntimeError("programming defect")))

    with pytest.raises(RuntimeError, match="programming defect"):
        asyncio.run(service(asyncio.Event()))

    assert persistence.defer_calls == []


def test_duplicate_provider_name_is_a_terminal_semantic_failure() -> None:
    persistence = _Persistence()
    ambiguity = ProviderSignalAmbiguity(SUBJECT.subject_id, SIGNAL)

    asyncio.run(_service(_claim(), persistence, _Poller(ambiguity))(asyncio.Event()))

    assert persistence.result is not None
    assert persistence.result.findings[0].kind == "ambiguous_provider_signal"
    assert persistence.defer_calls == []


def test_lost_claim_cannot_write_a_provider_observation() -> None:
    persistence = _Persistence()
    persistence.claim_lost = True

    asyncio.run(_service(_claim(), persistence, _Poller(_observation()))(asyncio.Event()))

    assert persistence.snapshot.revision == 0
    assert persistence.result is None


def test_abort_before_claim_performs_no_work() -> None:
    source = _Source((_claim(),))
    persistence = _Persistence()
    service = ReconciliationRoundService(
        source=source,
        persistence=persistence,
        poller=_Poller(_observation()),
        clock=FixedClock(NOW),
        convergence_policy=POLICY,
        max_claims=1,
    )
    abort = asyncio.Event()
    abort.set()

    asyncio.run(service(abort))

    assert source.calls == 0


def test_task_cancellation_is_not_translated_into_retry_or_failure() -> None:
    async def exercise() -> None:
        persistence = _Persistence()
        poller = _BlockingPoller()
        task = asyncio.create_task(_service(_claim(), persistence, poller)(asyncio.Event()))
        await poller.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert persistence.defer_calls == []
        assert persistence.result is None

    asyncio.run(exercise())


def test_terminal_projection_and_result_share_the_current_revision() -> None:
    persistence = _Persistence()
    terminal = _TerminalPersistence(persistence)

    asyncio.run(
        _service(
            _claim(),
            persistence,
            _Poller(_observation()),
            terminal=terminal,
        )(asyncio.Event())
    )

    assert len(terminal.calls) == 1
    assert terminal.calls[0][1] == 1


def test_round_requires_atomic_persistence_and_projection_as_one_capability() -> None:
    with pytest.raises(ValueError, match="configured together"):
        ReconciliationRoundService(
            source=_Source(()),
            persistence=_Persistence(),
            poller=_Poller(()),
            clock=FixedClock(NOW),
            convergence_policy=POLICY,
            max_claims=1,
            terminal_persistence=_TerminalPersistence(_Persistence()),
        )
