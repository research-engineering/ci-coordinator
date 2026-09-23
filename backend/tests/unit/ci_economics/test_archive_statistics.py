from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
    ArchivePopulation,
)

from .archive_factories import ARCHIVE_TIME, archived_job, archived_statistics


@pytest.mark.parametrize(
    ("population", "total", "count"),
    [
        ("complete", 0, 0),
        ("complete", 2, 2),
        ("partial", 3, 2),
        ("partial", 2, 0),
        ("partial", None, 1),
        ("unavailable", None, 0),
        ("conflict", 2, 1),
    ],
)
def test_statistical_population_preserves_zero_unknown_partial_and_conflicting(
    population: ArchivePopulation, total: int | None, count: int
) -> None:
    statistics = archived_statistics(
        population=population,
        provider_total=total,
        jobs=tuple(archived_job(index + 1) for index in range(count)),
    )
    recovered = ArchivedAttemptStatistics.model_validate_json(statistics.model_dump_json())
    assert recovered == statistics
    assert recovered.statistics_digest == statistics.statistics_digest
    assert recovered.population == population
    assert recovered.provider_job_total == total


@pytest.mark.parametrize(
    ("population", "total", "count"),
    [
        ("complete", None, 0),
        ("complete", 2, 1),
        ("partial", 1, 1),
        ("partial", 0, 0),
        ("partial", None, 0),
        ("unavailable", 0, 0),
        ("unavailable", None, 1),
        ("conflict", 0, 1),
    ],
)
def test_population_relations_cannot_claim_unobserved_completeness(
    population: ArchivePopulation, total: int | None, count: int
) -> None:
    with pytest.raises(ValidationError):
        archived_statistics(
            population=population,
            provider_total=total,
            jobs=tuple(archived_job(index + 1) for index in range(count)),
        )


@pytest.mark.parametrize("ids", [(1, 1), (2, 1)])
def test_job_identity_order_rejects_duplicate_or_reordered_contributions(
    ids: tuple[int, ...],
) -> None:
    with pytest.raises(ValidationError):
        archived_statistics(jobs=tuple(archived_job(index) for index in ids), provider_total=2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_job_id", True),
        ("created_at", ARCHIVE_TIME.replace(tzinfo=None)),
        ("runner_id", None),
        ("labels", ("linux", "linux")),
        ("name", ""),
        ("name", "name\x00suffix"),
        ("name", "name\ud800suffix"),
        ("conclusion", "not-a-conclusion"),
    ],
)
def test_typed_job_cannot_bypass_independent_scalar_or_relation_admission(
    field: str, value: object
) -> None:
    invalid = archived_job().model_copy(update={field: value})
    with pytest.raises(ValidationError):
        archived_statistics(jobs=(invalid,))


@pytest.mark.parametrize("field", ["runCreatedAt", "providerJobTotal", "workflowId"])
def test_archive_rejects_scalar_coercion(field: str) -> None:
    raw = archived_statistics().canonical_mapping()
    raw[field] = 0 if field == "runCreatedAt" else "1"
    with pytest.raises(ValidationError):
        ArchivedAttemptStatistics.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field", ["runnerName", "runnerGroupName", "steps", "logs", "raw"])
def test_permanent_job_allowlist_excludes_detail_and_arbitrary_provider_fields(field: str) -> None:
    raw = archived_job().model_dump(mode="json")
    raw[field] = None
    with pytest.raises(ValidationError):
        ArchivedJobStatistics.model_validate_json(json.dumps(raw))


def test_exact_statistical_operands_survive_without_detail_or_current_evidence() -> None:
    statistics = archived_statistics()
    job = statistics.jobs[0]
    assert statistics.workflow_blob_sha is None
    assert job.name == "Lint"
    assert job.timing is not None
    assert job.timing.started_at == ARCHIVE_TIME.replace(minute=1)
    assert job.timing.completed_at == ARCHIVE_TIME.replace(minute=2)
    assert job.runner_id == 7
    assert set(statistics.canonical_mapping()) == {
        "schemaVersion",
        "attempt",
        "workflowId",
        "workflowPath",
        "workflowBlobSha",
        "event",
        "conclusion",
        "runCreatedAt",
        "population",
        "providerJobTotal",
        "jobs",
    }


@pytest.mark.parametrize(
    ("created", "started", "completed", "quality"),
    [
        (3, 1, 2, "inconsistent"),
        (3, 3, 2, "inconsistent"),
        (1, 2, 3, "consistent"),
        (None, 2, 3, "incomplete"),
        (None, None, None, "incomplete"),
        (3, None, 2, "inconsistent"),
    ],
)
def test_archive_preserves_inconsistent_provider_facts_without_inventing_timing(
    created: int | None, started: int | None, completed: int | None, quality: str
) -> None:
    values = tuple(
        None if value is None else ARCHIVE_TIME + timedelta(minutes=value)
        for value in (created, started, completed)
    )
    raw = archived_job().model_dump()
    raw.update(zip(("createdAt", "startedAt", "completedAt"), values, strict=True))
    job = ArchivedJobStatistics.model_validate(raw)
    statistics = archived_statistics(jobs=(job,))
    recovered = ArchivedAttemptStatistics.model_validate_json(statistics.model_dump_json())
    assert recovered == statistics
    assert (job.created_at, job.started_at, job.completed_at) == values
    assert job.timing_quality == quality
    assert (job.timing is None) == (quality == "inconsistent")
    assert recovered.population == "complete"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event", "push"),
        ("conclusion", "failure"),
        ("workflowId", 405),
        ("workflowBlobSha", "b" * 40),
    ],
)
def test_each_run_operand_changes_the_statistical_digest(field: str, value: object) -> None:
    original = archived_statistics()
    raw = original.canonical_mapping()
    raw[field] = value
    changed = ArchivedAttemptStatistics.model_validate_json(json.dumps(raw))
    assert original.statistics_digest != changed.statistics_digest


def test_known_workflow_revision_cannot_lose_its_path() -> None:
    invalid = archived_statistics().model_copy(
        update={"workflow_blob_sha": "b" * 40, "workflow_path": None}
    )
    with pytest.raises(ValidationError):
        ArchivedAttemptStatistics.model_validate(invalid)


@pytest.mark.parametrize("field", ["createdAt", "startedAt", "completedAt"])
@pytest.mark.parametrize("value", [0, True, "2020-01-01T00:00:00", "not-a-date"])
def test_json_job_time_admits_only_aware_text_or_declared_null(field: str, value: object) -> None:
    raw = archived_job().model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        ArchivedJobStatistics.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field", ["createdAt", "startedAt", "completedAt"])
def test_python_job_time_does_not_admit_json_text(field: str) -> None:
    raw = archived_job().model_dump()
    raw[field] = ARCHIVE_TIME.isoformat()
    with pytest.raises(ValidationError):
        ArchivedJobStatistics.model_validate(raw)


@pytest.mark.parametrize("job_payload", [False, True])
def test_json_array_normalization_does_not_weaken_python_tuple_admission(
    job_payload: bool,
) -> None:
    if job_payload:
        raw = archived_job().model_dump()
        raw["labels"] = ["linux"]
        with pytest.raises(ValidationError):
            ArchivedJobStatistics.model_validate(raw)
    else:
        raw = archived_statistics().model_dump()
        raw["jobs"] = [archived_job()]
        with pytest.raises(ValidationError):
            ArchivedAttemptStatistics.model_validate(raw)


@pytest.mark.parametrize("job_payload", [False, True])
def test_json_sequence_rejects_scalar_substitution(job_payload: bool) -> None:
    if job_payload:
        raw = archived_job().model_dump(mode="json")
        raw["labels"] = "linux"
        with pytest.raises(ValidationError):
            ArchivedJobStatistics.model_validate_json(json.dumps(raw))
    else:
        raw = archived_statistics().canonical_mapping()
        raw["jobs"] = "empty"
        with pytest.raises(ValidationError):
            ArchivedAttemptStatistics.model_validate_json(json.dumps(raw))


def test_job_json_round_trip_preserves_optional_timestamps_and_labels() -> None:
    job = ArchivedJobStatistics.model_validate(
        {**archived_job().model_dump(), "createdAt": None, "labels": ()}
    )
    assert ArchivedJobStatistics.model_validate_json(job.model_dump_json()) == job
