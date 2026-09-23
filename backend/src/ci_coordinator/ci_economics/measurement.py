"""Pure, total CI economics derivations from retained exact evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ci_coordinator.ci_economics.model import (
    MEASUREMENT_DEFINITION_VERSION,
    AttemptEconomics,
    AttemptMeasurements,
    AttemptSnapshot,
    DurationAggregate,
    ProviderAttemptSnapshot,
    WorkflowJobFact,
)
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


def derive_attempt_economics(
    snapshot: AttemptSnapshot,
    webhook_jobs: tuple[WorkflowJobFact, ...],
) -> AttemptEconomics:
    """Derive exact values only where all required evidence agrees."""
    if type(snapshot) is not AttemptSnapshot:
        raise TypeError("economics derivation requires an exact attempt snapshot")
    measurements = derive_attempt_measurements(snapshot, webhook_jobs)
    return AttemptEconomics(
        snapshot=snapshot,
        definition_version=measurements.definition_version,
        queue=measurements.queue,
        runner_occupancy=measurements.runner_occupancy,
        attempt_wall=measurements.attempt_wall,
        observation_set_hash=measurements.observation_set_hash,
    )


def derive_attempt_measurements(
    snapshot: AttemptSnapshot | ProviderAttemptSnapshot,
    webhook_jobs: tuple[WorkflowJobFact, ...],
) -> AttemptMeasurements:
    if type(snapshot) not in {AttemptSnapshot, ProviderAttemptSnapshot}:
        raise TypeError("measurements require an exact provider or retained snapshot")
    if type(webhook_jobs) is not tuple or any(
        type(job) is not WorkflowJobFact for job in webhook_jobs
    ):
        raise TypeError("webhook jobs must be an exact tuple of facts")
    if any(job.attempt != snapshot.attempt for job in webhook_jobs):
        raise ValueError("webhook evidence belongs to another attempt")
    if any(job.delivery_id is None for job in webhook_jobs):
        raise ValueError("webhook evidence requires delivery provenance")

    observations_by_job: dict[int, list[WorkflowJobFact]] = defaultdict(list)
    for observation in webhook_jobs:
        observations_by_job[observation.provider_job_id].append(observation)

    snapshot_job_ids = {job.provider_job_id for job in snapshot.jobs}
    conflicting_observations = any(
        len({item.semantic_hash for item in observations}) > 1
        for observations in observations_by_job.values()
    )
    unknown_job = not set(observations_by_job).issubset(snapshot_job_ids)
    snapshot_by_job = {job.provider_job_id: job for job in snapshot.jobs}
    cross_source_conflict = any(
        _shared_projection(observations[0]) != _shared_projection(snapshot_by_job[provider_job_id])
        for provider_job_id, observations in observations_by_job.items()
        if provider_job_id in snapshot_by_job
    )
    conflict = conflicting_observations or unknown_job or cross_source_conflict
    observation_set_hash = hash_object(
        {
            "attempt": snapshot.attempt.canonical_mapping(),
            "observations": [
                {
                    "providerJobId": item.provider_job_id,
                    "semanticHash": item.semantic_hash,
                    "deliveryId": item.delivery_id,
                }
                for item in sorted(
                    webhook_jobs,
                    key=lambda item: (
                        item.provider_job_id,
                        item.semantic_hash,
                        item.delivery_id or "",
                    ),
                )
            ],
        }
    )
    if conflict:
        unavailable = DurationAggregate(
            quality="conflict",
            known_value_ms=None,
            known_job_count=0,
            total_job_count=len(snapshot.jobs),
            reason_code="conflicting_workflow_job_evidence",
        )
        return AttemptMeasurements(
            definition_version=MEASUREMENT_DEFINITION_VERSION,
            queue=unavailable,
            runner_occupancy=unavailable,
            attempt_wall=unavailable,
            observation_set_hash=observation_set_hash,
        )

    webhook_by_job = {
        provider_job_id: observations[0]
        for provider_job_id, observations in observations_by_job.items()
    }
    queue = _queue_duration(snapshot.jobs, webhook_by_job)
    occupancy = _runner_occupancy(snapshot.jobs)
    wall = _attempt_wall(snapshot.jobs, webhook_by_job)
    return AttemptMeasurements(
        definition_version=MEASUREMENT_DEFINITION_VERSION,
        queue=queue,
        runner_occupancy=occupancy,
        attempt_wall=wall,
        observation_set_hash=observation_set_hash,
    )


def _queue_duration(
    snapshot_jobs: tuple[WorkflowJobFact, ...],
    webhook_by_job: dict[int, WorkflowJobFact],
) -> DurationAggregate:
    values: list[int] = []
    for job in snapshot_jobs:
        observed = webhook_by_job.get(job.provider_job_id)
        if (
            observed is not None
            and observed.name == job.name
            and observed.timing.created_at is not None
            and observed.timing.started_at is not None
        ):
            values.append(_milliseconds(observed.timing.created_at, observed.timing.started_at))
    return _aggregate(values, len(snapshot_jobs), "workflow_job_queue_evidence_incomplete")


def _runner_occupancy(jobs: tuple[WorkflowJobFact, ...]) -> DurationAggregate:
    values = [
        _milliseconds(job.timing.started_at, job.timing.completed_at)
        for job in jobs
        if job.timing.started_at is not None and job.timing.completed_at is not None
    ]
    return _aggregate(values, len(jobs), "provider_job_occupancy_evidence_incomplete")


def _attempt_wall(
    snapshot_jobs: tuple[WorkflowJobFact, ...],
    webhook_by_job: dict[int, WorkflowJobFact],
) -> DurationAggregate:
    starts: list[datetime] = []
    completions: list[datetime] = []
    for job in snapshot_jobs:
        observed = webhook_by_job.get(job.provider_job_id)
        if (
            observed is None
            or observed.name != job.name
            or observed.timing.created_at is None
            or job.timing.completed_at is None
        ):
            continue
        starts.append(observed.timing.created_at)
        completions.append(job.timing.completed_at)
    total = len(snapshot_jobs)
    known = len(starts)
    if known != total:
        return DurationAggregate(
            quality="unknown",
            known_value_ms=None,
            known_job_count=known,
            total_job_count=total,
            reason_code="attempt_wall_evidence_incomplete",
        )
    duration = _milliseconds(min(starts), max(completions))
    if duration > MAX_SAFE_JSON_INTEGER:
        return _out_of_range(known, total)
    return DurationAggregate("exact", duration, known, total, None)


def _aggregate(values: list[int], total: int, incomplete_reason: str) -> DurationAggregate:
    known = len(values)
    value = sum(values)
    if value > MAX_SAFE_JSON_INTEGER:
        return _out_of_range(known, total)
    if known == total:
        return DurationAggregate("exact", value, known, total, None)
    if known:
        return DurationAggregate("partial", value, known, total, incomplete_reason)
    return DurationAggregate("unknown", None, 0, total, incomplete_reason)


def _out_of_range(known: int, total: int) -> DurationAggregate:
    return DurationAggregate(
        "unknown",
        None,
        known,
        total,
        "duration_sum_out_of_range",
    )


def _milliseconds(start: datetime, end: datetime) -> int:
    delta = end - start
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def _shared_projection(job: WorkflowJobFact) -> dict[str, object]:
    return {
        "attempt": job.attempt.canonical_mapping(),
        "providerJobId": job.provider_job_id,
        "name": job.name,
        "conclusion": job.conclusion,
        "startedAt": job.timing.canonical_mapping()["startedAt"],
        "completedAt": job.timing.canonical_mapping()["completedAt"],
        "labels": list(job.labels),
        "runner": job.runner.canonical_mapping(),
    }
