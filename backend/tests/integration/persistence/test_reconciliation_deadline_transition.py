from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.app.shadow_reconciliation import ShadowReconciliationProjector
from ci_coordinator.kernel import FixedClock
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresShadowReconciliationUnitOfWork,
    StoreUnavailable,
    TransactionalReconciliationStore,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.reconciliation_pair_repository import (
    _PostgresReconciliationPairRepository,
)
from ci_coordinator.persistence.reconciliation_state_repository import (
    _PostgresReconciliationRepository,
)
from ci_coordinator.persistence.schema import reconciliation_results, reconciliation_subjects
from ci_coordinator.reconciliation import (
    CandidateEvidenceContext,
    ObservationAppended,
    OmittedSignal,
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationConvergenceState,
    ReconciliationResult,
    ReconciliationSnapshot,
    ReconciliationSubject,
    ReconciliationTerminalRequired,
    ResultDuplicate,
    ResultRecord,
    ResultRecorded,
    ResultRevisionConflict,
    acquire_reconciliation_claim,
    convergence_failure,
    initial_convergence_state,
)

from ._reconciliation_support import (
    POLICY,
    claim,
    contract,
    database_time,
    observation,
    register,
    subject,
)
from .test_reconciliation_lease_authority import (
    _retained,
    _wait_for_lock,
    _wait_until_expired,
)

pytestmark = pytest.mark.persistence


async def _fixture_claim(
    engine: AsyncEngine, admin: AsyncEngine, *, deadline_passed: bool
) -> ReconciliationAttemptClaim:
    now = await database_time(admin)
    target = subject()
    expected_contract = replace(
        contract(),
        candidate_evidence=CandidateEvidenceContext(
            profile_id="deadline-shadow/v1",
            repository="example-org/ci-coordinator",
            config_epoch="epoch-1",
            policy_hash="policy-1",
            diff_hash="diff-1",
            graph_hash="graph-1",
            baseline_plan_id="full-ci-1",
            candidate_plan_id="candidate-1",
            omitted_signals=(OmittedSignal("backend-tests", "Backend tests"),),
        ),
    )
    deadline = now + timedelta(seconds=-10 if deadline_passed else 10)
    created_at = deadline - timedelta(seconds=POLICY.deadline_seconds)
    await register(engine, target, expected_contract, created_at=created_at)
    snapshot = ReconciliationSnapshot(target, expected_contract, 0, ())
    acquired = acquire_reconciliation_claim(
        initial_convergence_state(created_at, POLICY),
        target,
        expected_contract,
        snapshot.revision,
        worker_id="1" * 64,
        now=min(now, deadline) - timedelta(seconds=1),
        policy=replace(POLICY, lease_seconds=POLICY.deadline_seconds),
    )
    assert acquired is not None and acquired.claim.may_poll
    # Seed a previously acquired valid lease, as if its owner paused before defer.
    async with admin.begin() as connection:
        changed = await connection.execute(
            update(reconciliation_subjects)
            .where(reconciliation_subjects.c.subject_id == target.subject_id)
            .values(
                attempt_count=acquired.state.attempt_count,
                claim_generation=acquired.state.claim_generation,
                lease_token=acquired.state.lease_token,
                lease_acquired_at=acquired.state.lease_acquired_at,
                lease_expires_at=acquired.state.lease_expires_at,
            )
        )
        assert changed.rowcount == 1
    checked_at = await database_time(admin)
    assert acquired.claim.claimed_at < checked_at < acquired.claim.lease_expires_at
    assert (checked_at >= acquired.claim.deadline_at) is deadline_passed
    return acquired.claim


def _store(
    engine: AsyncEngine, acquired: ReconciliationAttemptClaim
) -> TransactionalReconciliationStore:
    return TransactionalReconciliationStore(
        lambda: PostgresShadowReconciliationUnitOfWork(engine),
        FixedClock(acquired.claimed_at),
        POLICY,
    )


async def _terminal(
    store: TransactionalReconciliationStore, required: ReconciliationTerminalRequired
) -> ResultRecord | ReconciliationClaimLost:
    result = convergence_failure(required.claim, required.reason)
    projector = ShadowReconciliationProjector(FixedClock(required.claim.claimed_at))
    assert required.claim.contract.candidate_evidence is not None
    assert (
        len(
            projector.project(
                required.claim,
                ReconciliationResult(required.claim.subject.subject_id, "success", ()),
            )
        )
        == 1
    )
    evidence = projector.project(required.claim, result)
    assert evidence == ()
    return await store.record_result_with_evidence(
        required.claim, required.snapshot_revision, result, evidence
    )


@pytest.mark.parametrize("deadline_passed", [False, True])
def test_defer_uses_locked_revision_and_preserves_the_before_deadline_control(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    deadline_passed: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            acquired = await _fixture_claim(engine, admin, deadline_passed=deadline_passed)
            store = _store(engine, acquired)
            progress = replace(
                observation(acquired.subject, "progress"), status="in_progress", conclusion=None
            )
            appended = await store.append_observation(acquired, 0, progress)
            assert isinstance(appended, ObservationAppended)
            assert appended.snapshot.revision == 1 and acquired.revision == 0
            before_state, before_counts = await _retained(engine)
            before_time = await database_time(admin)

            outcome = await store.defer_claim(acquired)

            after_state, after_counts = await _retained(engine)
            if not deadline_passed:
                assert isinstance(outcome, ReconciliationConvergenceState)
                assert after_counts == before_counts
                assert after_state["revision"] == 1
                assert after_state["lease_token"] is None
                assert outcome.backoff_seconds == POLICY.initial_backoff_seconds * 2
                assert before_time < outcome.next_attempt_at <= acquired.deadline_at
                assert after_state["attempt_count"] == before_state["attempt_count"]
                assert after_state["claim_generation"] == before_state["claim_generation"]
                return
            assert isinstance(outcome, ReconciliationTerminalRequired)
            assert outcome.claim is acquired
            assert outcome.snapshot_revision == appended.snapshot.revision
            assert (after_state, after_counts) == (before_state, before_counts)
            recorded = await _terminal(store, outcome)
            assert isinstance(recorded, ResultRecorded)
            assert recorded.revision == 1
            assert recorded.result.state == "failure"
            assert recorded.result.findings[0].kind == "reconciliation_timed_out"
            terminal_state, terminal_counts = await _retained(engine)
            assert terminal_state["lease_token"] is None
            assert terminal_state["lease_acquired_at"] is None
            assert terminal_state["lease_expires_at"] is None
            assert terminal_counts == (
                before_counts[0],
                before_counts[1] + 1,
                before_counts[2] + 1,
                before_counts[3],
            )
            assert isinstance(await _terminal(store, outcome), ResultDuplicate)
            assert await _retained(engine) == (terminal_state, terminal_counts)
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_read_only_terminal_requirement_is_not_returned_after_cleanup_failure(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            acquired = await _fixture_claim(engine, admin, deadline_passed=True)
            store = _store(engine, acquired)
            before = await _retained(engine)
            original = PostgresShadowReconciliationUnitOfWork._finalize

            async def failed_cleanup(transaction: PostgresShadowReconciliationUnitOfWork) -> None:
                await original(transaction)
                raise StoreUnavailable("injected cleanup failure")

            monkeypatch.setattr(PostgresShadowReconciliationUnitOfWork, "_finalize", failed_cleanup)
            with pytest.raises(StoreUnavailable, match="finalization failed"):
                await store.defer_claim(acquired)
            assert await _retained(engine) == before
            monkeypatch.setattr(PostgresShadowReconciliationUnitOfWork, "_finalize", original)
            assert isinstance(await store.defer_claim(acquired), ReconciliationTerminalRequired)
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_defer_observes_deadline_only_after_real_subject_lock_and_never_commits_requirement(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            acquired = await _fixture_claim(engine, admin, deadline_passed=False)
            store = _store(engine, acquired)
            before = await _retained(engine)
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
            original = _PostgresReconciliationRepository.defer_claim

            async def observe(
                repository: _PostgresReconciliationRepository, holder: ReconciliationAttemptClaim
            ) -> (
                ReconciliationConvergenceState
                | ReconciliationClaimLost
                | ReconciliationTerminalRequired
            ):
                await repository._connection.scalar(
                    select(func.set_config("lock_timeout", "20s", True))
                )
                pid = await repository._connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                waiter_pid.set_result(pid)
                return await original(repository, holder)

            async def reject_commit(_transaction: PostgresShadowReconciliationUnitOfWork) -> None:
                raise AssertionError("a terminal requirement is read-only, not a committed effect")

            monkeypatch.setattr(_PostgresReconciliationRepository, "defer_claim", observe)
            monkeypatch.setattr(PostgresShadowReconciliationUnitOfWork, "commit", reject_commit)
            async with asyncio.timeout(30), asyncio.TaskGroup() as group:
                async with admin.begin() as connection:
                    await connection.execute(select(reconciliation_subjects).with_for_update())
                    holder_pid = await connection.scalar(select(func.pg_backend_pid()))
                    assert type(holder_pid) is int
                    pending = group.create_task(store.defer_claim(acquired))
                    await _wait_for_lock(admin, await waiter_pid, holder_pid)
                    assert await database_time(admin) < acquired.deadline_at
                    await _wait_until_expired(admin, acquired.deadline_at)
                    assert await database_time(admin) < acquired.lease_expires_at
                required = await pending
            assert isinstance(required, ReconciliationTerminalRequired)
            assert required.claim is acquired
            assert required.snapshot_revision == before[0]["revision"]
            assert await _retained(engine) == before
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["revision", "expiry", "reclaim", "cas-expiry"])
def test_terminal_requirement_rechecks_revision_and_each_lease_authority_stage(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    change: Literal["revision", "expiry", "reclaim", "cas-expiry"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            acquired = await _fixture_claim(engine, admin, deadline_passed=True)
            store = _store(engine, acquired)
            required = await store.defer_claim(acquired)
            assert isinstance(required, ReconciliationTerminalRequired)
            if change == "revision":
                appended = await store.append_observation(
                    acquired, required.snapshot_revision, observation(acquired.subject, "late")
                )
                assert isinstance(appended, ObservationAppended)
            else:
                expiry = await database_time(admin) - timedelta(seconds=1)
                assert acquired.claimed_at < expiry < acquired.lease_expires_at
                async with admin.begin() as connection:
                    changed = await connection.execute(
                        update(reconciliation_subjects)
                        .where(reconciliation_subjects.c.subject_id == acquired.subject.subject_id)
                        .values(lease_expires_at=expiry)
                    )
                    assert changed.rowcount == 1
                if change == "reclaim":
                    replacement = await claim(engine, "2" * 64)
                    assert replacement is not None
                    assert replacement.generation == acquired.generation + 1
                    assert replacement.token != acquired.token
            cas_results: list[bool] = []
            if change == "cas-expiry":
                original = _PostgresReconciliationRepository._update_convergence

                async def stale_sample(_repository: _PostgresReconciliationRepository) -> datetime:
                    return acquired.claimed_at

                async def observed_cas(
                    repository: _PostgresReconciliationRepository,
                    holder: ReconciliationAttemptClaim,
                    state: ReconciliationConvergenceState,
                ) -> bool:
                    changed = await original(repository, holder, state)
                    cas_results.append(changed)
                    return changed

                monkeypatch.setattr(
                    _PostgresReconciliationRepository, "_database_time", stale_sample
                )
                monkeypatch.setattr(
                    _PostgresReconciliationRepository, "_update_convergence", observed_cas
                )
            before = await _retained(engine)
            outcome = await _terminal(store, required)
            if change == "revision":
                assert isinstance(outcome, ResultRevisionConflict)
                assert outcome.snapshot.revision == required.snapshot_revision + 1
            else:
                assert isinstance(outcome, ReconciliationClaimLost)
            if change == "cas-expiry":
                assert cas_results == [False]
            assert await _retained(engine) == before
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["cancel", "audit"])
def test_terminal_requirement_rolls_back_real_result_and_lease_mutations(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["cancel", "audit"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            acquired = await _fixture_claim(engine, admin, deadline_passed=True)
            store = _store(engine, acquired)
            required = await store.defer_claim(acquired)
            assert isinstance(required, ReconciliationTerminalRequired)
            before = await _retained(engine)
            mutated = asyncio.Event()
            original = _PostgresReconciliationPairRepository._append_terminal_result

            async def after_mutation(
                repository: _PostgresReconciliationPairRepository,
                target: ReconciliationSubject,
                recorded: ResultRecord,
                occurred_at: datetime,
            ) -> bool:
                del target, occurred_at
                assert isinstance(recorded, ResultRecorded)
                connection = repository._state._connection
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(reconciliation_results)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(select(reconciliation_subjects.c.lease_token)) is None
                )
                mutated.set()
                if failure == "audit":
                    raise PersistenceInvariantViolation("injected terminal audit failure")
                await asyncio.Event().wait()
                raise AssertionError("cancelled terminal mutation resumed")

            monkeypatch.setattr(
                _PostgresReconciliationPairRepository, "_append_terminal_result", after_mutation
            )
            async with asyncio.timeout(20):
                if failure == "cancel":
                    async with asyncio.TaskGroup() as group:
                        pending = group.create_task(_terminal(store, required))
                        await mutated.wait()
                        pending.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await pending
                else:
                    with pytest.raises(
                        PersistenceInvariantViolation, match="injected terminal audit"
                    ):
                        await _terminal(store, required)
            assert mutated.is_set()
            assert await _retained(engine) == before
            monkeypatch.setattr(
                _PostgresReconciliationPairRepository, "_append_terminal_result", original
            )
            assert isinstance(await _terminal(store, required), ResultRecorded)
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
