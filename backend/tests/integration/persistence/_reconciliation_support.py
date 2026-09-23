from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.persistence import PostgresShadowReconciliationUnitOfWork
from ci_coordinator.persistence.schema import reconciliation_subjects
from ci_coordinator.reconciliation import (
    PlanningEvidenceContext,
    ReconciliationAttemptClaim,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationSubject,
    SignalObservation,
    SubjectRegistered,
    initial_convergence_state,
)

POLICY = ReconciliationConvergencePolicy(
    deadline_seconds=60,
    initial_backoff_seconds=2,
    max_backoff_seconds=8,
    max_attempts=3,
    poll_timeout_seconds=2,
    lease_seconds=10,
)
SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)


async def database_time(engine: AsyncEngine) -> datetime:
    async with engine.connect() as connection:
        value = await connection.scalar(select(func.clock_timestamp()))
    assert type(value) is datetime
    return value


async def register(
    engine: AsyncEngine,
    subject: ReconciliationSubject,
    expected_contract: ReconciliationContract | None = None,
    *,
    created_at: datetime | None = None,
) -> None:
    now = await database_time(engine) if created_at is None else created_at
    async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
        outcome = await transaction.reconciliation.register_subject(
            subject, expected_contract or contract(), initial_convergence_state(now, POLICY)
        )
        assert isinstance(outcome, SubjectRegistered)
        await transaction.commit()


async def claim(
    engine: AsyncEngine,
    worker_id: str,
    *,
    policy: ReconciliationConvergencePolicy = POLICY,
) -> ReconciliationAttemptClaim | None:
    async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
        outcome = await transaction.reconciliation.claim_next(worker_id=worker_id, policy=policy)
        if outcome is not None:
            await transaction.commit()
        return outcome


async def expire_claim(
    admin_engine: AsyncEngine, acquired: ReconciliationAttemptClaim
) -> ReconciliationAttemptClaim:
    shift = acquired.lease_expires_at - acquired.claimed_at + timedelta(seconds=1)
    async with admin_engine.begin() as connection:
        result = await connection.execute(
            update(reconciliation_subjects)
            .where(reconciliation_subjects.c.subject_id == acquired.subject.subject_id)
            .values(
                {
                    name: reconciliation_subjects.c[name] - shift
                    for name in (
                        "created_at",
                        "deadline_at",
                        "next_attempt_at",
                        "lease_acquired_at",
                        "lease_expires_at",
                    )
                }
            )
        )
        assert result.rowcount == 1
    return replace(
        acquired,
        claimed_at=acquired.claimed_at - shift,
        lease_expires_at=acquired.lease_expires_at - shift,
        deadline_at=acquired.deadline_at - shift,
    )


def subject() -> ReconciliationSubject:
    return ReconciliationSubject.create(
        installation_id=101,
        repository_id=202,
        event_name="pull_request",
        ref="refs/pull/42/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=303,
        run_attempt=2,
    )


def contract(
    *, signal: ProviderSignal = SIGNAL, with_planning_evidence: bool = True
) -> ReconciliationContract:
    evidence = (
        PlanningEvidenceContext(
            request_hash="1" * 64,
            input_hash="2" * 64,
            config_epoch_id="3" * 64,
            repo_epoch_hash="4" * 64,
            diff_hash="5" * 64,
            policy_hash="6" * 64,
            graph_hash="7" * 64,
            validation_catalog_hash="8" * 64,
            deterministic_plan_id="candidate-plan-1",
            verified_plan_id="verified-plan-1",
            verified_plan_hash="9" * 64,
            planner_version="planning-core/v1",
            verifier_version="verification-core/v1",
            fallback_reason=None,
        )
        if with_planning_evidence
        else None
    )
    return ReconciliationContract((signal,), (), planning_evidence=evidence)


def observation(subject: ReconciliationSubject, observation_id: str) -> SignalObservation:
    return SignalObservation(
        observation_id=observation_id,
        subject_id=subject.subject_id,
        signal_id=SIGNAL.signal_id,
        workflow_run_id=subject.workflow_run_id,
        run_attempt=subject.run_attempt,
        provider_job_id=401,
        status="completed",
        conclusion="success",
    )
