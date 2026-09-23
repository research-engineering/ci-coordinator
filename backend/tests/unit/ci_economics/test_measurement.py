from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics import (
    AttemptSnapshot,
    ProviderAttemptSnapshot,
    RunnerIdentity,
    WorkflowJobFact,
)
from ci_coordinator.ci_economics.measurement import (
    derive_attempt_economics,
    derive_attempt_measurements,
)
from ci_coordinator.ci_economics.model import AttemptMeasurements, DurationAggregate
from ci_coordinator.kernel import hash_object

from .factories import ATTEMPT, NOW, job, retained_snapshot


@pytest.mark.parametrize(
    "observations,qualities,values",
    [
        ((job(),), ("exact", "exact", "exact"), (60_000, 120_000, 180_000)),
        ((), ("unknown", "exact", "unknown"), (None, 120_000, None)),
        ((job(name="other"),), ("conflict", "conflict", "conflict"), (None, None, None)),
        ((job(provider_job_id=405),), ("conflict", "conflict", "conflict"), (None, None, None)),
    ],
)
def test_independent_provider_measurements_preserve_the_legacy_formulas_without_a_contract(
    observations: tuple[WorkflowJobFact, ...],
    qualities: tuple[str, ...],
    values: tuple[int | None, ...],
) -> None:
    retained = retained_snapshot(job(created_at=None, delivery_id=None))
    independent = ProviderAttemptSnapshot(
        "f" * 64,
        retained.attempt,
        retained.snapshot_digest,
        retained.jobs,
    )
    measured = derive_attempt_measurements(independent, observations)
    legacy = derive_attempt_economics(retained, observations)
    aggregates = (measured.queue, measured.runner_occupancy, measured.attempt_wall)

    assert tuple(item.quality for item in aggregates) == qualities
    assert tuple(item.known_value_ms for item in aggregates) == values
    assert measured == AttemptMeasurements(
        legacy.definition_version,
        legacy.queue,
        legacy.runner_occupancy,
        legacy.attempt_wall,
        legacy.observation_set_hash,
    )
    assert measured == derive_attempt_measurements(retained, observations)
    with pytest.raises(TypeError, match="exact attempt snapshot"):
        derive_attempt_economics(cast(AttemptSnapshot, independent), observations)


def test_shared_measurements_reject_non_snapshot_and_incoherent_aggregates() -> None:
    with pytest.raises(TypeError, match="exact provider or retained snapshot"):
        derive_attempt_measurements(cast(ProviderAttemptSnapshot, object()), ())
    independent = ProviderAttemptSnapshot(
        "f" * 64,
        ATTEMPT,
        retained_snapshot(job(created_at=None, delivery_id=None)).snapshot_digest,
        (job(created_at=None, delivery_id=None),),
    )
    measurements = derive_attempt_measurements(independent, ())
    with pytest.raises(ValueError, match="coverage crosses"):
        replace(measurements, queue=DurationAggregate("unknown", None, 0, 2, "missing"))
    with pytest.raises(ValueError, match="conflict must invalidate"):
        replace(measurements, attempt_wall=DurationAggregate("conflict", None, 0, 1, "conflict"))


@pytest.mark.parametrize("operand", ["definition_version", "observation_set_hash"])
def test_shared_measurement_identity_is_admitted(operand: str) -> None:
    measured = derive_attempt_measurements(
        retained_snapshot(job(created_at=None, delivery_id=None)),
        (),
    )
    with pytest.raises(ValueError):
        if operand == "definition_version":
            replace(measured, definition_version="future")
        else:
            replace(measured, observation_set_hash="")


def test_consistent_sources_produce_exact_declared_durations() -> None:
    provider_job = job(created_at=None, delivery_id=None)
    economics = derive_attempt_economics(retained_snapshot(provider_job), (job(),))

    assert economics.queue.quality == "exact"
    assert economics.queue.known_value_ms == 60_000
    assert economics.runner_occupancy.quality == "exact"
    assert economics.runner_occupancy.known_value_ms == 120_000
    assert economics.attempt_wall.quality == "exact"
    assert economics.attempt_wall.known_value_ms == 180_000


@pytest.mark.parametrize(
    "conflicting_job",
    [
        job(name="frontend"),
        job(conclusion="failure"),
        job(started_at=NOW + timedelta(minutes=2)),
        job(completed_at=NOW + timedelta(minutes=4)),
        job(labels=("linux",)),
        job(runner=RunnerIdentity(21, "runner-b", 22, "linux")),
    ],
)
def test_mutating_any_shared_source_fact_invalidates_every_exact_aggregate(
    conflicting_job: WorkflowJobFact,
) -> None:
    provider_job = job(created_at=None, delivery_id=None)

    economics = derive_attempt_economics(
        retained_snapshot(provider_job),
        (conflicting_job,),
    )

    assert {
        economics.queue.quality,
        economics.runner_occupancy.quality,
        economics.attempt_wall.quality,
    } == {"conflict"}


@pytest.mark.parametrize(
    "observations",
    [
        (job(), job(created_at=NOW - timedelta(seconds=1), delivery_id="delivery-2")),
        (job(provider_job_id=405),),
    ],
)
def test_contradictory_or_unknown_webhook_evidence_is_fail_closed(
    observations: tuple[WorkflowJobFact, ...],
) -> None:
    economics = derive_attempt_economics(
        retained_snapshot(job(created_at=None, delivery_id=None)),
        observations,
    )

    assert economics.queue.quality == "conflict"
    assert economics.queue.known_value_ms is None


def test_absent_webhook_evidence_does_not_weaken_exact_provider_occupancy() -> None:
    economics = derive_attempt_economics(
        retained_snapshot(job(created_at=None, delivery_id=None)),
        (),
    )

    assert economics.queue.quality == "unknown"
    assert economics.runner_occupancy.quality == "exact"
    assert economics.attempt_wall.quality == "unknown"


def test_provider_only_fact_cannot_masquerade_as_webhook_evidence() -> None:
    provider_job = job(created_at=None, delivery_id=None)

    with pytest.raises(ValueError, match="delivery provenance"):
        derive_attempt_economics(retained_snapshot(provider_job), (provider_job,))


def test_observation_set_identity_binds_delivery_provenance() -> None:
    provider_job = job(created_at=None, delivery_id=None)
    first = derive_attempt_economics(retained_snapshot(provider_job), (job(),))
    second_observation = job(delivery_id="delivery-2")
    second = derive_attempt_economics(retained_snapshot(provider_job), (second_observation,))

    assert first.observation_set_hash != second.observation_set_hash
    assert second.observation_set_hash == hash_object(
        {
            "attempt": provider_job.attempt.canonical_mapping(),
            "observations": [
                {
                    "providerJobId": second_observation.provider_job_id,
                    "semanticHash": second_observation.semantic_hash,
                    "deliveryId": "delivery-2",
                }
            ],
        }
    )


def test_duration_sum_outside_json_safe_range_is_explicitly_unavailable() -> None:
    started_at = datetime.min.replace(tzinfo=UTC)
    completed_at = datetime.max.replace(tzinfo=UTC)
    provider_jobs = tuple(
        job(
            provider_job_id=job_id,
            created_at=None,
            started_at=started_at,
            completed_at=completed_at,
            delivery_id=None,
        )
        for job_id in range(1, 31)
    )
    webhook_jobs = tuple(
        job(
            provider_job_id=job_id,
            created_at=started_at,
            started_at=started_at,
            completed_at=completed_at,
            delivery_id=f"delivery-{job_id}",
        )
        for job_id in range(1, 31)
    )

    economics = derive_attempt_economics(retained_snapshot(*provider_jobs), webhook_jobs)

    assert economics.runner_occupancy.quality == "unknown"
    assert economics.runner_occupancy.known_value_ms is None
    assert economics.runner_occupancy.known_job_count == 30
    assert economics.runner_occupancy.reason_code == "duration_sum_out_of_range"


@pytest.mark.parametrize(
    ("snapshot", "observations", "error", "message"),
    [
        (
            cast(AttemptSnapshot, object()),
            (),
            TypeError,
            "exact attempt snapshot",
        ),
        (
            retained_snapshot(),
            cast(tuple[WorkflowJobFact, ...], [job()]),
            TypeError,
            "exact tuple of facts",
        ),
        (
            retained_snapshot(),
            (job(attempt=replace(ATTEMPT, workflow_run_id=ATTEMPT.workflow_run_id + 1)),),
            ValueError,
            "another attempt",
        ),
    ],
)
def test_derivation_rejects_evidence_outside_the_exact_attempt_boundary(
    snapshot: AttemptSnapshot,
    observations: tuple[WorkflowJobFact, ...],
    error: type[BaseException],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        derive_attempt_economics(snapshot, observations)


def test_partial_webhook_coverage_is_reported_as_a_partial_sum_only() -> None:
    provider_jobs = (
        job(provider_job_id=404, created_at=None, delivery_id=None),
        job(provider_job_id=405, created_at=None, delivery_id=None),
    )

    economics = derive_attempt_economics(
        retained_snapshot(*provider_jobs),
        (job(provider_job_id=404),),
    )

    assert economics.queue.quality == "partial"
    assert economics.queue.known_value_ms == 60_000
    assert economics.queue.known_job_count == 1
    assert economics.queue.total_job_count == 2
    assert economics.attempt_wall.quality == "unknown"
