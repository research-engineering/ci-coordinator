from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ci_coordinator.ci_economics import (
    AttemptIdentity,
    AttemptSnapshot,
    CollectionPolicy,
    JobTiming,
    ProviderAttemptSnapshot,
    RunnerIdentity,
    WorkflowJobFact,
)
from ci_coordinator.ci_economics.catalog import RecordedProviderSource
from ci_coordinator.ci_economics.collection import initial_collection_state
from ci_coordinator.ci_economics.measurement import derive_attempt_measurements
from ci_coordinator.ci_economics.model import WorkflowConclusion
from ci_coordinator.ci_economics.read_models import (
    IndependentAttemptSnapshot,
    RecordedAttemptEconomics,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.kernel import hash_object
from ci_coordinator.reconciliation import ReconciliationContract, ReconciliationSubject

NOW = datetime(2026, 9, 4, 10, tzinfo=UTC)
SUBJECT = ReconciliationSubject.create(
    installation_id=101,
    repository_id=202,
    event_name="push",
    ref="refs/heads/main",
    base_sha="a" * 40,
    head_sha="b" * 40,
    workflow_run_id=303,
    run_attempt=2,
)
ATTEMPT = AttemptIdentity(
    RepositoryScope(SUBJECT.installation_id, SUBJECT.repository_id),
    SUBJECT.workflow_run_id,
    SUBJECT.run_attempt,
    SUBJECT.head_sha,
)
SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
CONTRACT = ReconciliationContract((SIGNAL,), ())
POLICY = CollectionPolicy(
    collection_window_seconds=600,
    initial_backoff_seconds=10,
    maximum_backoff_seconds=80,
    maximum_attempts=3,
    lease_seconds=30,
    evidence_days=1,
    tombstone_grace_seconds=300,
)
RUNNER = RunnerIdentity(11, "runner-a", 12, "linux")


def recorded_source(attempt: AttemptIdentity = ATTEMPT) -> RecordedProviderSource:
    source = ProviderRunCollectionSource(attempt, NOW, "2026-03-10", "f" * 64)
    return RecordedProviderSource(
        source, initial_collection_state(source.source_id, NOW, NOW, POLICY)
    )


def job(
    *,
    attempt: AttemptIdentity = ATTEMPT,
    provider_job_id: int = 404,
    name: str = "backend",
    conclusion: WorkflowConclusion = "success",
    created_at: datetime | None = NOW,
    started_at: datetime | None = NOW + timedelta(minutes=1),
    completed_at: datetime | None = NOW + timedelta(minutes=3),
    labels: tuple[str, ...] = ("linux", "self-hosted"),
    runner: RunnerIdentity = RUNNER,
    delivery_id: str | None = "delivery-1",
) -> WorkflowJobFact:
    timing = JobTiming(created_at, started_at, completed_at)
    canonical = {
        "attempt": attempt.canonical_mapping(),
        "providerJobId": provider_job_id,
        "name": name,
        "conclusion": conclusion,
        "timing": timing.canonical_mapping(),
        "labels": list(labels),
        "runner": runner.canonical_mapping(),
    }
    return WorkflowJobFact(
        attempt=attempt,
        provider_job_id=provider_job_id,
        name=name,
        conclusion=conclusion,
        timing=timing,
        labels=labels,
        runner=runner,
        semantic_hash=hash_object(canonical),
        delivery_id=delivery_id,
    )


def provider_snapshot(*jobs: WorkflowJobFact) -> ProviderAttemptSnapshot:
    facts = tuple(sorted(jobs or (job(created_at=None, delivery_id=None),), key=_job_id))
    return ProviderAttemptSnapshot(
        SUBJECT.subject_id,
        ATTEMPT,
        _snapshot_digest(facts),
        facts,
    )


def retained_snapshot(*jobs: WorkflowJobFact) -> AttemptSnapshot:
    facts = tuple(sorted(jobs or (job(created_at=None, delivery_id=None),), key=_job_id))
    return AttemptSnapshot(
        subject_id=SUBJECT.subject_id,
        attempt=ATTEMPT,
        contract_hash=CONTRACT.contract_hash,
        planned_route="unknown",
        recorded_at=NOW + timedelta(minutes=4),
        retain_until=NOW + timedelta(days=1),
        snapshot_digest=_snapshot_digest(facts),
        jobs=facts,
    )


def _snapshot_digest(jobs: tuple[WorkflowJobFact, ...]) -> str:
    return hash_object(
        {
            "attempt": ATTEMPT.canonical_mapping(),
            "jobs": [item.canonical_mapping() for item in jobs],
        }
    )


def recorded_measurements(
    *, independent: bool = False, attempt: AttemptIdentity = ATTEMPT
) -> RecordedAttemptEconomics:
    facts = (job(attempt=attempt, delivery_id=None),)
    digest = hash_object(
        {"attempt": attempt.canonical_mapping(), "jobs": [facts[0].canonical_mapping()]}
    )
    if independent:
        source = ProviderRunCollectionSource(attempt, NOW, "2026-03-10", "f" * 64)
        evidence = ProviderAttemptSnapshot(source.source_id, attempt, digest, facts)
        snapshot: AttemptSnapshot | IndependentAttemptSnapshot = IndependentAttemptSnapshot(
            source, evidence, NOW + timedelta(minutes=4), NOW + timedelta(days=1)
        )
        measurements = derive_attempt_measurements(evidence, ())
    else:
        snapshot = AttemptSnapshot(
            SUBJECT.subject_id,
            attempt,
            CONTRACT.contract_hash,
            "unknown",
            NOW + timedelta(minutes=4),
            NOW + timedelta(days=1),
            digest,
            facts,
        )
        measurements = derive_attempt_measurements(snapshot, ())
    return RecordedAttemptEconomics(snapshot, measurements)


def _job_id(value: WorkflowJobFact) -> int:
    return value.provider_job_id
