from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.kernel import FixedClock
from ci_coordinator.persistence import (
    PersistenceInvariantViolation,
    PostgresShadowReconciliationUnitOfWork,
    TransactionalReconciliationStore,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.production_cutover_schema_attestation import (
    shadow_reconciliation_v2_schema_matches_contract,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    reconciliation_results,
    reconciliation_subjects,
    shadow_evidence,
)
from ci_coordinator.reconciliation import (
    ObservationAppended,
    ReconciliationClaimLost,
    ReconciliationResult,
    ReconciliationSubject,
    ResultDuplicate,
    ResultRecorded,
    SubjectRegistered,
    SubjectRegistrationDuplicate,
    convergence_failure,
)
from ci_coordinator.shadow_mode import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    ShadowCandidate,
    ShadowEvidenceConflict,
    ShadowEvidenceDuplicate,
    ShadowEvidenceRecord,
    ShadowEvidenceStored,
    compare_full_ci,
)

from ._audit_replay_support import load_test_audit_records
from ._reconciliation_support import POLICY
from ._reconciliation_support import claim as _claim
from ._reconciliation_support import contract as _contract
from ._reconciliation_support import database_time as _database_time
from ._reconciliation_support import expire_claim as _expire_claim
from ._reconciliation_support import observation as _observation
from ._reconciliation_support import register as _register
from ._reconciliation_support import subject as _subject

pytestmark = pytest.mark.persistence

NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)
NATIVE_SIGNAL = ProviderSignal.declared_native(
    workflow_path=".github/workflows/full-check.yml",
    job_id="full-check-gate",
    job_name="Full Check",
)


def test_shadow_evidence_restart_preserves_first_timestamp(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        first = _shadow_record("candidate-a", NOW)
        replay = _shadow_record("candidate-a", NOW + timedelta(hours=1))
        conflict = _shadow_record("candidate-b", NOW + timedelta(hours=2))
        try:
            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                assert isinstance(
                    await transaction.shadow_evidence.record(first),
                    ShadowEvidenceStored,
                )
                await transaction.commit()
            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                duplicate = await transaction.shadow_evidence.record(replay)
                retained = await transaction.shadow_evidence.list_records("shadow-profile/v1")
                await transaction.rollback()
            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                conflicting = await transaction.shadow_evidence.record(conflict)
                await transaction.rollback()

            assert isinstance(duplicate, ShadowEvidenceDuplicate)
            assert duplicate.record.observed_at == NOW
            assert retained == (first,)
            assert isinstance(conflicting, ShadowEvidenceConflict)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_two_replicas_cannot_claim_one_due_subject(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        try:
            await _register(engine, subject)
            left, right = await asyncio.gather(
                _claim(engine, "1" * 64),
                _claim(engine, "2" * 64),
            )
            claims = tuple(item for item in (left, right) if item is not None)
            assert len(claims) == 1
            assert claims[0].attempt_count == 1
            assert claims[0].generation == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_expired_lease_reclaims_and_fences_the_stale_replica(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        subject = _subject()
        contract = _contract()
        try:
            await _register(engine, subject, contract)
            stale = await _claim(engine, "1" * 64)
            assert stale is not None
            stale = await _expire_claim(admin_engine, stale)
            current = await _claim(engine, "2" * 64)
            assert current is not None
            assert current.generation == stale.generation + 1

            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                rejected = await transaction.reconciliation.append_observation(
                    stale,
                    0,
                    _observation(subject, "stale"),
                )
                accepted = await transaction.reconciliation.append_observation(
                    current,
                    0,
                    _observation(subject, "current"),
                )
                await transaction.commit()

            assert isinstance(rejected, ReconciliationClaimLost)
            assert isinstance(accepted, ObservationAppended)
        finally:
            await admin_engine.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_native_gate_contract_round_trips_through_durable_reconciliation(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        contract = _contract(signal=NATIVE_SIGNAL)
        try:
            await _register(engine, subject, contract)
            claim = await _claim(engine, "1" * 64)

            assert claim is not None
            assert claim.subject == subject
            assert claim.contract == contract
            assert claim.contract.provider_signals == (NATIVE_SIGNAL,)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_terminal_timeout_can_be_claimed_long_after_deadline(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        before = await _database_time(engine)
        try:
            await _register(engine, subject, created_at=before - timedelta(days=2))
            claim = await _claim(engine, "1" * 64)
            assert claim is not None
            assert claim.terminal_reason == "deadline_exceeded"
            assert before <= claim.claimed_at <= await _database_time(engine)
            assert claim.lease_expires_at == claim.claimed_at + timedelta(
                seconds=POLICY.lease_seconds
            )

            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                recorded = await transaction.reconciliation.record_result(
                    claim,
                    0,
                    convergence_failure(claim, "deadline_exceeded"),
                )
                await transaction.commit()

            assert isinstance(recorded, ResultRecorded)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_defer_persists_due_time_attempt_and_backoff_across_restart(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin_engine = create_postgres_engine(postgres_database_url)
        subject = _subject()
        try:
            await _register(engine, subject)
            first = await _claim(engine, "1" * 64)
            assert first is not None
            before = await _database_time(engine)
            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                deferred = await transaction.reconciliation.defer_claim(first)
                await transaction.commit()
            assert not isinstance(deferred, ReconciliationClaimLost)
            after = await _database_time(engine)
            assert before + timedelta(seconds=1) <= deferred.next_attempt_at
            assert deferred.next_attempt_at <= after + timedelta(
                seconds=POLICY.initial_backoff_seconds
            )
            assert deferred.backoff_seconds == POLICY.initial_backoff_seconds * 2

            async with admin_engine.begin() as connection:
                await connection.execute(
                    update(reconciliation_subjects)
                    .where(reconciliation_subjects.c.subject_id == subject.subject_id)
                    .values(next_attempt_at=reconciliation_subjects.c.deadline_at)
                )
            before_due = await _claim(engine, "2" * 64)
            assert await _database_time(engine) < deferred.deadline_at
            async with admin_engine.begin() as connection:
                await connection.execute(
                    update(reconciliation_subjects)
                    .where(reconciliation_subjects.c.subject_id == subject.subject_id)
                    .values(next_attempt_at=func.clock_timestamp())
                )
            at_due = await _claim(engine, "2" * 64)
            assert before_due is None
            assert at_due is not None
            assert at_due.attempt_count == 2
        finally:
            await admin_engine.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_terminal_result_audit_and_shadow_evidence_are_one_idempotent_transaction(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        contract = _contract()
        result = ReconciliationResult(subject.subject_id, "success", ())
        evidence = _shadow_record_for_subject(subject, "candidate-a", NOW)

        def unit_of_work() -> PostgresShadowReconciliationUnitOfWork:
            return PostgresShadowReconciliationUnitOfWork(engine)

        store = TransactionalReconciliationStore(
            unit_of_work, FixedClock(await _database_time(engine)), POLICY
        )
        try:
            assert isinstance(await store.register_subject(subject, contract), SubjectRegistered)
            claim = await _claim(engine, "1" * 64)
            assert claim is not None
            assert isinstance(
                await store.record_result_with_evidence(claim, 0, result, (evidence,)),
                ResultRecorded,
            )
            assert isinstance(
                await store.record_result_with_evidence(claim, 0, result, (evidence,)),
                ResultDuplicate,
            )

            async with unit_of_work() as transaction:
                records = await transaction.shadow_evidence.list_records("shadow-profile/v1")
                events = await load_test_audit_records(transaction.audit_events)
                await transaction.rollback()
            assert records == (evidence,)
            assert tuple(event.event_type for event in events) == (
                "dynamic-ci-plan.verified",
                "reconciliation-state.terminal",
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_shadow_conflict_rolls_back_result_and_lease_release(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        result = ReconciliationResult(subject.subject_id, "success", ())
        retained = _shadow_record_for_subject(subject, "candidate-a", NOW)
        conflicting = _shadow_record_for_subject(
            subject,
            "candidate-b",
            NOW + timedelta(seconds=1),
        )

        def unit_of_work() -> PostgresShadowReconciliationUnitOfWork:
            return PostgresShadowReconciliationUnitOfWork(engine)

        store = TransactionalReconciliationStore(
            unit_of_work, FixedClock(await _database_time(engine)), POLICY
        )
        try:
            assert isinstance(
                await store.register_subject(subject, _contract()),
                SubjectRegistered,
            )
            claim = await _claim(engine, "1" * 64)
            assert claim is not None
            async with unit_of_work() as transaction:
                assert isinstance(
                    await transaction.shadow_evidence.record(retained),
                    ShadowEvidenceStored,
                )
                await transaction.commit()

            with pytest.raises(PersistenceInvariantViolation, match="retained reconciliation"):
                await store.record_result_with_evidence(claim, 0, result, (conflicting,))

            assert isinstance(await store.record_result(claim, 0, result), ResultRecorded)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "retained_effects",
    ["result", "result-audit", "result-audit-shadow", "conflict-first", "conflict-last"],
)
def test_terminal_replay_cannot_complete_or_extend_a_partial_effect(
    runtime_postgres_database_url: str,
    retained_effects: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        result = ReconciliationResult(subject.subject_id, "success", ())
        evidence = _shadow_record_for_subject(subject, "candidate-a", NOW)

        def unit_of_work() -> PostgresShadowReconciliationUnitOfWork:
            return PostgresShadowReconciliationUnitOfWork(engine)

        async def retained() -> tuple[tuple[tuple[object, ...], ...], ...]:
            rows = []
            async with engine.connect() as connection:
                for table in (
                    reconciliation_subjects,
                    reconciliation_results,
                    audit_events,
                    shadow_evidence,
                ):
                    selected = await connection.execute(
                        select(table).order_by(*table.primary_key.columns)
                    )
                    rows.append(tuple(tuple(row) for row in selected))
            return tuple(rows)

        store = TransactionalReconciliationStore(
            unit_of_work, FixedClock(await _database_time(engine)), POLICY
        )
        try:
            await store.register_subject(subject, _contract())
            claim = await _claim(engine, "1" * 64)
            assert claim is not None
            if retained_effects == "result":
                async with unit_of_work() as transaction:
                    recorded = await transaction.reconciliation.record_result(claim, 0, result)
                    assert isinstance(recorded, ResultRecorded)
                    await transaction.commit()
            elif retained_effects == "result-audit":
                assert isinstance(await store.record_result(claim, 0, result), ResultRecorded)
            else:
                assert isinstance(
                    await store.record_result_with_evidence(claim, 0, result, (evidence,)),
                    ResultRecorded,
                )
            before = await retained()
            attempted = (
                (evidence, _shadow_record_for_subject(subject, "candidate-a", NOW, surface="ui"))
                if retained_effects == "result-audit-shadow"
                else (evidence,)
            )
            if retained_effects in {"conflict-first", "conflict-last"}:
                conflicting = _shadow_record_for_subject(subject, "candidate-b", NOW)
                new_record = _shadow_record_for_subject(subject, "candidate-a", NOW, surface="ui")
                attempted = (
                    (conflicting, new_record)
                    if retained_effects == "conflict-first"
                    else (new_record, conflicting)
                )
                with pytest.raises(PersistenceInvariantViolation, match="retained reconciliation"):
                    await store.record_result_with_evidence(claim, 0, result, attempted)
            else:
                outcome = await store.record_result_with_evidence(claim, 0, result, attempted)
                assert isinstance(outcome, ReconciliationClaimLost)
            assert await retained() == before
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_registration_and_planning_audit_replay_as_one_transaction(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        subject = _subject()
        contract = _contract(with_planning_evidence=True)

        def unit_of_work() -> PostgresShadowReconciliationUnitOfWork:
            return PostgresShadowReconciliationUnitOfWork(engine)

        first = TransactionalReconciliationStore(unit_of_work, FixedClock(NOW), POLICY)
        replay = TransactionalReconciliationStore(
            unit_of_work,
            FixedClock(NOW + timedelta(hours=1)),
            POLICY,
        )
        try:
            assert isinstance(await first.register_subject(subject, contract), SubjectRegistered)
            assert isinstance(
                await replay.register_subject(subject, contract),
                SubjectRegistrationDuplicate,
            )
            async with unit_of_work() as transaction:
                events = await load_test_audit_records(transaction.audit_events)
                await transaction.rollback()
            assert len(events) == 1
            assert events[0].event_type == "dynamic-ci-plan.verified"
            assert events[0].created_at == "2026-07-17T12:00:00.000Z"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_catalog_attestation_rejects_a_weakened_lease_constraint(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await shadow_reconciliation_v2_schema_matches_contract(connection)
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.reconciliation_subjects DROP CONSTRAINT "
                            "ck_reconciliation_subjects_lease_shape"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.reconciliation_subjects ADD CONSTRAINT "
                            "ck_reconciliation_subjects_lease_shape CHECK (lease_token IS NULL)"
                        )
                    )
                    assert not await shadow_reconciliation_v2_schema_matches_contract(connection)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("lease_token", "lease_acquired_at", "lease_expires_at"),
    [
        (None, NOW, NOW + timedelta(seconds=1)),
        ("1" * 64, NOW, NOW + timedelta(seconds=3_601)),
    ],
    ids=["partial", "overlong"],
)
def test_database_rejects_invalid_lease_shapes(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    lease_token: str | None,
    lease_acquired_at: datetime,
    lease_expires_at: datetime,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        owner_engine = create_postgres_engine(postgres_database_url)
        subject = _subject()
        try:
            await _register(runtime_engine, subject)

            with pytest.raises(IntegrityError):
                async with owner_engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE ci_coordinator.reconciliation_subjects "
                            "SET lease_token = :lease_token, "
                            "lease_acquired_at = :lease_acquired_at, "
                            "lease_expires_at = :lease_expires_at "
                            "WHERE subject_id = :subject_id"
                        ),
                        {
                            "lease_token": lease_token,
                            "lease_acquired_at": lease_acquired_at,
                            "lease_expires_at": lease_expires_at,
                            "subject_id": subject.subject_id,
                        },
                    )
        finally:
            await runtime_engine.dispose()
            await owner_engine.dispose()

    asyncio.run(scenario())


def _shadow_record(plan: str, observed_at: datetime) -> ShadowEvidenceRecord:
    candidate = ShadowCandidate(
        repo="example-org/ci-coordinator",
        event="delivery-42",
        base_sha="a" * 40,
        head_sha="b" * 40,
        config_epoch="epoch-1",
        policy_hash="policy-1",
        diff_hash="diff-1",
        graph_hash="graph-1",
        baseline_plan="baseline-plan-1",
        candidate_plan=plan,
        surface="pull-request",
        coverage_relation=CoverageRelation.COVERED,
        actual_full_ci_result=FullCiResult.PASSED,
    )
    return _evidence(candidate, observed_at)


def _shadow_record_for_subject(
    subject: ReconciliationSubject,
    plan: str,
    observed_at: datetime,
    *,
    surface: str = "backend-tests",
) -> ShadowEvidenceRecord:
    candidate = ShadowCandidate(
        repo="example-org/ci-coordinator",
        event=subject.subject_id,
        base_sha=subject.base_sha,
        head_sha=subject.head_sha,
        config_epoch="epoch-1",
        policy_hash="policy-1",
        diff_hash="diff-1",
        graph_hash="graph-1",
        baseline_plan="baseline-plan-1",
        candidate_plan=plan,
        surface=surface,
        coverage_relation=CoverageRelation.COVERED,
        actual_full_ci_result=FullCiResult.PASSED,
    )
    return _evidence(candidate, observed_at)


def _evidence(candidate: ShadowCandidate, observed_at: datetime) -> ShadowEvidenceRecord:
    observation = FullCiObservation(
        repo=candidate.repo,
        event=candidate.event,
        base_sha=candidate.base_sha,
        head_sha=candidate.head_sha,
        config_epoch=candidate.config_epoch,
        policy_hash=candidate.policy_hash,
        diff_hash=candidate.diff_hash,
        graph_hash=candidate.graph_hash,
        baseline_plan=candidate.baseline_plan,
        candidate_plan=candidate.candidate_plan,
        actual_full_ci_result=candidate.actual_full_ci_result,
    )
    return ShadowEvidenceRecord(
        "shadow-profile/v1",
        observed_at,
        compare_full_ci(candidate, observation),
    )
