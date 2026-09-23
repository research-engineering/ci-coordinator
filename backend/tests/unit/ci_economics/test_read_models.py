from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from ci_coordinator.ci_economics.measurement import derive_attempt_measurements
from ci_coordinator.ci_economics.model import (
    AttemptMeasurements,
    AttemptSnapshot,
    ProviderAttemptSnapshot,
)
from ci_coordinator.ci_economics.read_models import IndependentAttemptSnapshot
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_economics.factories import ATTEMPT, NOW, job, provider_snapshot, recorded_measurements


@pytest.mark.parametrize("independent", (False, True))
def test_retained_measurements_bind_both_source_kinds(independent: bool) -> None:
    result = recorded_measurements(independent=independent)
    assert result.attempt == ATTEMPT
    assert result.measurements.queue.quality == "unknown"
    assert result.measurements.queue.known_value_ms is None
    assert result.measurements.queue.known_job_count == 0
    assert result.measurements.queue.total_job_count == 1
    assert result.measurements.queue.reason_code == "workflow_job_queue_evidence_incomplete"
    assert result.measurements.runner_occupancy.known_value_ms == 120_000
    assert result.measurements.attempt_wall.quality == "unknown"
    assert result.measurements.attempt_wall.known_value_ms is None
    assert result.measurements.attempt_wall.reason_code == "attempt_wall_evidence_incomplete"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("source", None, TypeError),
        ("evidence", None, TypeError),
        ("recorded_at", NOW.replace(tzinfo=None), ValueError),
        ("retain_until", NOW.replace(tzinfo=None), ValueError),
        ("recorded_at", NOW - timedelta(microseconds=1), ValueError),
        ("recorded_at", NOW + timedelta(days=1), ValueError),
        ("retain_until", NOW + timedelta(minutes=4), ValueError),
    ],
)
def test_independent_snapshot_rejects_invalid_bindings(
    field: str, value: object, error: type[Exception]
) -> None:
    snapshot = recorded_measurements(independent=True).snapshot
    assert isinstance(snapshot, IndependentAttemptSnapshot)
    with pytest.raises(error):
        if field == "source":
            replace(snapshot, source=cast(ProviderRunCollectionSource, value))
        elif field == "evidence":
            replace(snapshot, evidence=cast(ProviderAttemptSnapshot, value))
        elif field == "recorded_at":
            replace(snapshot, recorded_at=cast(datetime, value))
        else:
            assert field == "retain_until"
            replace(snapshot, retain_until=cast(datetime, value))


def test_independent_snapshot_rejects_another_provenance_source() -> None:
    snapshot = recorded_measurements(independent=True).snapshot
    assert isinstance(snapshot, IndependentAttemptSnapshot)
    other = recorded_measurements(independent=True, attempt=replace(ATTEMPT, run_attempt=3))
    assert isinstance(other.snapshot, IndependentAttemptSnapshot)
    with pytest.raises(ValueError, match="source identity"):
        replace(snapshot, source=other.snapshot.source)
    with pytest.raises(ValueError, match="source identity"):
        replace(snapshot, evidence=replace(snapshot.evidence, subject_id="a" * 64))


def test_independent_retention_normalizes_instants_without_changing_identity() -> None:
    snapshot = recorded_measurements(independent=True).snapshot
    changed = replace(
        snapshot,
        recorded_at=snapshot.recorded_at.astimezone(timezone(timedelta(hours=2))),
        retain_until=snapshot.retain_until.astimezone(timezone(timedelta(hours=-3))),
    )
    assert changed == snapshot
    assert changed.recorded_at.tzinfo == NOW.tzinfo
    assert changed.retain_until.tzinfo == NOW.tzinfo


@pytest.mark.parametrize("field", ("snapshot", "measurements"))
def test_retained_measurements_reject_untyped_operands(field: str) -> None:
    with pytest.raises(TypeError):
        if field == "snapshot":
            replace(recorded_measurements(), snapshot=cast(AttemptSnapshot, None))
        else:
            replace(recorded_measurements(), measurements=cast(AttemptMeasurements, None))


def test_retained_measurements_reject_another_job_cardinality() -> None:
    result = recorded_measurements(independent=True)
    snapshot = result.snapshot
    assert isinstance(snapshot, IndependentAttemptSnapshot)
    evidence = provider_snapshot(
        *snapshot.evidence.jobs, job(provider_job_id=405, delivery_id=None)
    )
    with pytest.raises(ValueError, match="cardinality"):
        replace(result, measurements=derive_attempt_measurements(evidence, ()))
