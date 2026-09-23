from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics._observation_values import positive_id
from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_HEADER_BYTES,
    MAX_HISTORY_JOB_BYTES,
    MAX_HISTORY_STATISTICS_BYTES,
    encode_archive_header,
    encode_archive_job,
)
from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
)
from ci_coordinator.ci_economics.model import MAX_JOBS_PER_ATTEMPT
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    require_bytes,
)


@dataclass(frozen=True, slots=True)
class EncodedArchiveStatistics:
    attempt: dict[str, object]
    jobs: tuple[dict[str, object], ...]
    canonical_bytes: int


def encode_archive_statistics(
    statistics: ArchivedAttemptStatistics, *, generation: int
) -> EncodedArchiveStatistics:
    positive_id(generation, "archive dataset generation")
    statistics = ArchivedAttemptStatistics.model_validate(statistics)
    identity = statistics.attempt.to_attempt()
    key: dict[str, object] = {
        "installation_id": identity.scope.installation_id,
        "repository_id": identity.scope.repository_id,
        "generation": generation,
        "workflow_run_id": identity.workflow_run_id,
        "run_attempt": identity.run_attempt,
    }
    raw_header = encode_archive_header(statistics)
    jobs = tuple(_encode_job(job, key) for job in statistics.jobs)
    byte_count = len(raw_header) + sum(
        len(require_bytes(row["job_canonical"], "archived job")) for row in jobs
    )
    if byte_count > MAX_HISTORY_STATISTICS_BYTES:
        raise ValueError("archive statistics exceed the per-attempt canonical byte budget")
    return EncodedArchiveStatistics(
        {
            **key,
            "head_sha": identity.head_sha,
            "workflow_id": statistics.workflow_id,
            "run_created_at": statistics.run_created_at,
            "header_canonical": raw_header,
            "statistics_digest": statistics.statistics_digest,
            "job_count": len(jobs),
            "statistics_bytes": byte_count,
        },
        jobs,
        byte_count,
    )


def decode_archive_statistics(
    row: Mapping[str, object] | RowMapping, job_rows: tuple[Mapping[str, object] | RowMapping, ...]
) -> ArchivedAttemptStatistics:
    if type(job_rows) is not tuple or len(job_rows) > MAX_JOBS_PER_ATTEMPT:
        raise ValueError("stored archive jobs exceed their bounded read")
    header = decode_canonical_object(
        row.get("header_canonical"),
        maximum_bytes=MAX_HISTORY_HEADER_BYTES,
        context="archive statistics header",
    )
    if "jobs" in header or type(header.get("runCreatedAt")) is not str:
        raise ValueError("stored archive header has invalid normalized fields")
    created_at = header["runCreatedAt"]
    if not isinstance(created_at, str):
        raise ValueError("stored archive source time must be textual")
    header["runCreatedAt"] = datetime.fromisoformat(created_at)
    header["jobs"] = tuple(_decode_job(job) for job in job_rows)
    statistics = ArchivedAttemptStatistics.model_validate(header)
    generation = row.get("generation")
    if type(generation) is not int:
        raise ValueError("stored archive generation must be an exact integer")
    encoded = encode_archive_statistics(statistics, generation=generation)
    _match_columns(row, encoded.attempt)
    for actual, expected in zip(job_rows, encoded.jobs, strict=True):
        _match_columns(actual, expected)
    return statistics


def _encode_job(job: ArchivedJobStatistics, key: Mapping[str, object]) -> dict[str, object]:
    return {
        **key,
        "provider_job_id": job.provider_job_id,
        "name": job.name,
        "conclusion": job.conclusion,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "job_canonical": encode_archive_job(job),
    }


def _decode_job(row: Mapping[str, object] | RowMapping) -> ArchivedJobStatistics:
    raw = require_bytes(row.get("job_canonical"), "archive job statistics")
    decode_canonical_object(
        raw, maximum_bytes=MAX_HISTORY_JOB_BYTES, context="archive job statistics"
    )
    return ArchivedJobStatistics.model_validate_json(raw)


def _match_columns(
    actual: Mapping[str, object] | RowMapping, expected: Mapping[str, object]
) -> None:
    for name, value in expected.items():
        observed = actual.get(name)
        if type(value) is bytes:
            observed = require_bytes(observed, f"archived {name}")
        if type(observed) is not type(value) or observed != value:
            raise ValueError(f"stored archive projection contradicts canonical {name}")
