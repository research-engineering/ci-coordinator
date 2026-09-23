from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.model import (
    MAX_JOB_LABEL_CODE_POINTS,
    MAX_JOB_LABELS,
    MAX_JOB_NAME_CODE_POINTS,
    MAX_JOBS_PER_ATTEMPT,
    JobTiming,
    WorkflowConclusion,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel, JsonTuple
from ci_coordinator.ci_economics.report_payload import ReportAttemptPayload
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_DATETIME_ADAPTER = TypeAdapter(datetime)


def _json_instant(value: object, info: ValidationInfo) -> object:
    if info.mode == "json" and type(value) is str:
        return _DATETIME_ADAPTER.validate_python(value)
    return value


type ArchiveInstant = Annotated[datetime, BeforeValidator(_json_instant), AfterValidator(utc_time)]
type _PositiveId = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]
type _Label = Annotated[str, Field(min_length=1, max_length=MAX_JOB_LABEL_CODE_POINTS)]
type ArchivePopulation = Literal["complete", "partial", "unavailable", "conflict"]


class ArchivedJobStatistics(EconomicsPayloadModel):
    provider_job_id: _PositiveId = Field(alias="providerJobId")
    name: str = Field(min_length=1, max_length=MAX_JOB_NAME_CODE_POINTS)
    conclusion: WorkflowConclusion
    created_at: ArchiveInstant | None = Field(alias="createdAt")
    started_at: ArchiveInstant | None = Field(alias="startedAt")
    completed_at: ArchiveInstant | None = Field(alias="completedAt")
    labels: JsonTuple[_Label] = Field(max_length=MAX_JOB_LABELS)
    runner_id: _PositiveId | None = Field(alias="runnerId")
    runner_group_id: _PositiveId | None = Field(alias="runnerGroupId")

    @field_validator("name")
    @classmethod
    def admit_storable_name(cls, value: str) -> str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("archived job name requires storable Unicode scalar text")
        return value

    @model_validator(mode="after")
    def admit_relations(self) -> Self:
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("archived runner labels must be distinct")
        if self.runner_id is None and self.runner_group_id is not None:
            raise ValueError("archived runner group requires a runner identity")
        return self

    @property
    def timing(self) -> JobTiming | None:
        try:
            return JobTiming(self.created_at, self.started_at, self.completed_at)
        except ValueError:
            return None

    @property
    def timing_quality(self) -> Literal["consistent", "incomplete", "inconsistent"]:
        if self.timing is None:
            return "inconsistent"
        if None in (self.created_at, self.started_at, self.completed_at):
            return "incomplete"
        return "consistent"


class ArchivedAttemptHeader(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-archive-statistics/v1"] = Field(alias="schemaVersion")
    attempt: ReportAttemptPayload
    workflow_id: _PositiveId = Field(alias="workflowId")
    workflow_path: str | None = Field(alias="workflowPath", min_length=1, max_length=1024)
    workflow_blob_sha: str | None = Field(
        alias="workflowBlobSha", min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"
    )
    event: str = Field(min_length=1, max_length=64)
    conclusion: WorkflowConclusion | None
    run_created_at: ArchiveInstant = Field(alias="runCreatedAt")
    population: ArchivePopulation
    provider_job_total: int | None = Field(alias="providerJobTotal", ge=0, le=MAX_SAFE_JSON_INTEGER)

    @model_validator(mode="after")
    def admit_header(self) -> Self:
        _ = self.attempt.to_attempt()
        if self.workflow_blob_sha is not None and self.workflow_path is None:
            raise ValueError("known workflow bytes require their exact path")
        return self

    def admit_job_count(self, count: int) -> None:
        if type(count) is not int or not 0 <= count <= MAX_JOBS_PER_ATTEMPT:
            raise ValueError("archived job count exceeds the bounded population")
        if self.provider_job_total is not None and count > self.provider_job_total:
            raise ValueError("archived job count exceeds the declared population")
        if self.population == "complete" and self.provider_job_total != count:
            raise ValueError("complete archive population requires an exact declared total")
        if self.population == "partial" and (
            (not count and self.provider_job_total is None) or self.provider_job_total == count
        ):
            raise ValueError("partial archive population requires evidence of an unknown remainder")
        if self.population == "unavailable" and (count or self.provider_job_total is not None):
            raise ValueError("unavailable archive population cannot assert a job population")


class ArchivedAttemptStatistics(ArchivedAttemptHeader):
    jobs: JsonTuple[ArchivedJobStatistics] = Field(max_length=MAX_JOBS_PER_ATTEMPT)

    @model_validator(mode="after")
    def admit_population(self) -> Self:
        job_ids = tuple(job.provider_job_id for job in self.jobs)
        if job_ids != tuple(sorted(set(job_ids))):
            raise ValueError("archived jobs must have unique, ordered provider identities")
        self.admit_job_count(len(self.jobs))
        return self

    def canonical_mapping(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def statistics_digest(self) -> str:
        return hash_object(self.canonical_mapping())
