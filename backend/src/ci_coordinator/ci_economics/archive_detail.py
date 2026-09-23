from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.model import MAX_JOBS_PER_ATTEMPT, WorkflowConclusion
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel, JsonTuple
from ci_coordinator.ci_economics.report_payload import ReportAttemptPayload
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER, bounded_canonical_json

MAX_HISTORY_DETAIL_BYTES: Final = 262_144
MAX_HISTORY_STEPS_PER_JOB: Final = 256

type _PositiveOrdinal = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]


class ArchiveStepDetail(EconomicsPayloadModel):
    number: _PositiveOrdinal
    status: Literal["completed"]
    conclusion: WorkflowConclusion | None
    started_at: ArchiveInstant | None = Field(alias="startedAt")
    completed_at: ArchiveInstant | None = Field(alias="completedAt")


class ArchivedJobDetail(EconomicsPayloadModel):
    provider_job_id: _PositiveOrdinal = Field(alias="providerJobId")
    steps: JsonTuple[ArchiveStepDetail] = Field(max_length=MAX_HISTORY_STEPS_PER_JOB)

    @model_validator(mode="after")
    def admit_step_order(self) -> ArchivedJobDetail:
        numbers = tuple(step.number for step in self.steps)
        if numbers != tuple(sorted(set(numbers))):
            raise ValueError("archive detail steps must have unique ordered ordinals")
        return self


class ArchivedAttemptDetail(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-archive-detail/v1"] = Field(alias="schemaVersion")
    attempt: ReportAttemptPayload
    jobs: JsonTuple[ArchivedJobDetail] = Field(max_length=MAX_JOBS_PER_ATTEMPT)

    @model_validator(mode="after")
    def admit_job_order(self) -> ArchivedAttemptDetail:
        job_ids = tuple(job.provider_job_id for job in self.jobs)
        if job_ids != tuple(sorted(set(job_ids))):
            raise ValueError("archive detail jobs must have unique ordered provider identities")
        return self


def validate_history_detail(statistics: object, detail: object) -> ArchivedAttemptDetail:
    from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics

    if type(statistics) is not ArchivedAttemptStatistics:
        raise TypeError("archive detail requires exact archived attempt statistics")
    if type(detail) is not ArchivedAttemptDetail:
        raise TypeError("archive detail requires an exact detail payload")
    if statistics.population != "complete":
        raise ValueError("archive detail requires a complete statistics population")
    if detail.attempt != statistics.attempt:
        raise ValueError("archive detail attempt identity differs from statistics")
    statistics_job_ids = tuple(job.provider_job_id for job in statistics.jobs)
    detail_job_ids = tuple(job.provider_job_id for job in detail.jobs)
    if detail_job_ids != statistics_job_ids:
        raise ValueError("archive detail job identities differ from statistics")
    return detail


def encode_archive_detail(detail: ArchivedAttemptDetail) -> bytes:
    detail = ArchivedAttemptDetail.model_validate(detail)
    return bounded_canonical_json(
        detail.model_dump(mode="json"), max_bytes=MAX_HISTORY_DETAIL_BYTES
    )


__all__ = (
    "MAX_HISTORY_DETAIL_BYTES",
    "MAX_HISTORY_STEPS_PER_JOB",
    "ArchiveStepDetail",
    "ArchivedAttemptDetail",
    "ArchivedJobDetail",
    "encode_archive_detail",
    "validate_history_detail",
)
