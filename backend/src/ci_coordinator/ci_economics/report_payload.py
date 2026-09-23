from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.ci_economics.reports import (
    CounterUnavailableReason,
    JobMeasurementReport,
    ReportCounter,
    ReportedWorkload,
    ReportMeasurement,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type _Digest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
type _PositiveId = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]


class ReportAttemptPayload(EconomicsPayloadModel):
    installation_id: _PositiveId = Field(alias="installationId")
    repository_id: _PositiveId = Field(alias="repositoryId")
    workflow_run_id: _PositiveId = Field(alias="workflowRunId")
    run_attempt: _PositiveId = Field(alias="runAttempt")
    head_sha: str = Field(alias="headSha", min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")

    def to_attempt(self) -> AttemptIdentity:
        return AttemptIdentity(
            RepositoryScope(self.installation_id, self.repository_id),
            self.workflow_run_id,
            self.run_attempt,
            self.head_sha,
        )


class ReportWorkloadPayload(EconomicsPayloadModel):
    protected_inputs_digest: _Digest = Field(alias="protectedInputsDigest")
    runner_class_digest: _Digest = Field(alias="runnerClassDigest")
    cache_class_digest: _Digest = Field(alias="cacheClassDigest")

    def to_workload(self) -> ReportedWorkload:
        return ReportedWorkload(
            self.protected_inputs_digest, self.runner_class_digest, self.cache_class_digest
        )


class ReportCounterPayload(EconomicsPayloadModel):
    counter: ReportCounter
    unit: Literal["microsecond"]
    scope: Literal["waited_children", "reporter_interval"]
    value: int | None = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    unavailable_reason: CounterUnavailableReason | None = Field(alias="unavailableReason")

    def to_measurement(self) -> ReportMeasurement:
        measurement = ReportMeasurement(self.counter, self.value, self.unavailable_reason)
        if measurement.scope != self.scope:
            raise ValueError("reported scope differs from its counter definition")
        return measurement


class JobMeasurementPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-job-report/v1"] = Field(alias="schemaVersion")
    method: Literal["waited_children/v1"]
    attempt: ReportAttemptPayload
    provider_job_id: _PositiveId = Field(alias="providerJobId")
    check_run_id: _PositiveId = Field(alias="checkRunId")
    sample_key: str = Field(
        alias="sampleKey", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    producer_digest: _Digest = Field(alias="producerDigest")
    workload: ReportWorkloadPayload
    reported_at: str = Field(
        alias="reportedAt",
        min_length=27,
        max_length=27,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$",
        json_schema_extra={"format": "date-time"},
    )
    command_exit_code: int = Field(
        alias="commandExitCode", ge=-MAX_SAFE_JSON_INTEGER, le=MAX_SAFE_JSON_INTEGER
    )
    measurements: list[ReportCounterPayload] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def admit_domain(self) -> Self:
        _ = self.to_report()
        return self

    def to_report(self) -> JobMeasurementReport:
        return JobMeasurementReport(
            attempt=self.attempt.to_attempt(),
            provider_job_id=self.provider_job_id,
            check_run_id=self.check_run_id,
            sample_key=self.sample_key,
            producer_digest=self.producer_digest,
            workload=self.workload.to_workload(),
            reported_at=datetime.fromisoformat(self.reported_at),
            command_exit_code=self.command_exit_code,
            measurements=tuple(item.to_measurement() for item in self.measurements),
        )
