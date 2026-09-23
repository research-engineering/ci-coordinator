from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.ci_economics import (
    AttemptIdentity,
    JobTiming,
    ProviderAttemptSnapshot,
    RunnerIdentity,
    WorkflowJobFact,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence import (
    PostgresCiEconomicsUnitOfWork,
    PostgresShadowReconciliationUnitOfWork,
    TransactionalCiEconomicsStore,
)
from ci_coordinator.reconciliation import (
    ReconciliationAttemptClaim,
    ReconciliationContract,
    ReconciliationConvergencePolicy,
    ReconciliationResult,
    ReconciliationSubject,
    ResultRecorded,
    SubjectRegistered,
    initial_convergence_state,
)

_RECONCILIATION_POLICY = ReconciliationConvergencePolicy(
    deadline_seconds=60,
    initial_backoff_seconds=1,
    max_backoff_seconds=4,
    max_attempts=3,
    poll_timeout_seconds=1,
    lease_seconds=10,
)
_SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
_CONTRACT = ReconciliationContract((_SIGNAL,), ())


def store(engine: AsyncEngine) -> TransactionalCiEconomicsStore:
    return TransactionalCiEconomicsStore(lambda: PostgresCiEconomicsUnitOfWork(engine))


async def database_now(engine: AsyncEngine) -> datetime:
    async with engine.connect() as connection:
        value = await connection.scalar(select(func.statement_timestamp()))
    assert type(value) is datetime
    return value.astimezone(UTC)


def provider_source(run: int, now: datetime) -> ProviderRunCollectionSource:
    return ProviderRunCollectionSource(
        AttemptIdentity(RepositoryScope(101, 202), run, 1, "a" * 40),
        now,
        "2026-03-10",
        "b" * 64,
    )


async def terminalize_reconciliation(
    engine: AsyncEngine,
    subject: ReconciliationSubject,
    now: datetime,
) -> None:
    async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
        registered = await transaction.reconciliation.register_subject(
            subject,
            _CONTRACT,
            initial_convergence_state(now, _RECONCILIATION_POLICY),
        )
        assert isinstance(registered, SubjectRegistered)
        await transaction.commit()
    async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
        claim = await transaction.reconciliation.claim_next(
            worker_id="f" * 64,
            policy=_RECONCILIATION_POLICY,
        )
        assert isinstance(claim, ReconciliationAttemptClaim)
        await transaction.commit()
    async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
        recorded = await transaction.reconciliation.record_result(
            claim,
            0,
            ReconciliationResult(subject.subject_id, "success", ()),
        )
        assert isinstance(recorded, ResultRecorded)
        await transaction.commit()


def subject(workflow_run_id: int, *, base_sha: str = "a" * 40) -> ReconciliationSubject:
    return ReconciliationSubject.create(
        installation_id=101,
        repository_id=202,
        event_name="pull_request",
        ref=f"refs/pull/{workflow_run_id}/merge",
        base_sha=base_sha,
        head_sha=f"{workflow_run_id:040x}",
        workflow_run_id=workflow_run_id,
        run_attempt=1,
    )


def subject_scope(subject: ReconciliationSubject) -> RepositoryScope:
    return RepositoryScope(subject.installation_id, subject.repository_id)


def snapshot(subject: ReconciliationSubject, now: datetime) -> ProviderAttemptSnapshot:
    attempt = AttemptIdentity(
        subject_scope(subject),
        subject.workflow_run_id,
        subject.run_attempt,
        subject.head_sha,
    )
    timing = JobTiming(None, now, now + timedelta(seconds=3))
    runner = RunnerIdentity(11, "runner-a", 12, "linux")
    labels = ("linux", "self-hosted")
    mapping = {
        "attempt": attempt.canonical_mapping(),
        "providerJobId": 404,
        "name": "Backend tests",
        "conclusion": "success",
        "timing": timing.canonical_mapping(),
        "labels": list(labels),
        "runner": runner.canonical_mapping(),
    }
    job = WorkflowJobFact(
        attempt=attempt,
        provider_job_id=404,
        name="Backend tests",
        conclusion="success",
        timing=timing,
        labels=labels,
        runner=runner,
        semantic_hash=hash_object(mapping),
        delivery_id=None,
    )
    return ProviderAttemptSnapshot(
        subject_id=subject.subject_id,
        attempt=attempt,
        snapshot_digest=hash_object(
            {
                "attempt": attempt.canonical_mapping(),
                "jobs": [job.canonical_mapping()],
            }
        ),
        jobs=(job,),
    )
