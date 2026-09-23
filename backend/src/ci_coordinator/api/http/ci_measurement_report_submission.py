from __future__ import annotations

from typing import Literal

from pydantic import Field

from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.ci_economics.report_ingestion import MAX_REPORT_JOB_PAGES
from ci_coordinator.ci_economics.report_payload import JobMeasurementPayload


class MeasurementReportSubmission(RequestModel):
    repository: str = Field(
        min_length=3,
        max_length=140,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}$",
    )
    ref: str = Field(min_length=1, max_length=1_024)
    event_name: str = Field(validation_alias="eventName", min_length=1, max_length=64)
    execution_sha: str = Field(
        validation_alias="executionSha", min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"
    )
    job_page: int = Field(validation_alias="jobPage", ge=1, le=MAX_REPORT_JOB_PAGES)
    report: JobMeasurementPayload


class MeasurementReportReceipt(ResponseModel):
    schema_version: Literal["ci-economics-report-receipt/v1"] = "ci-economics-report-receipt/v1"
    ok: Literal[True] = True
    status: Literal["recorded", "replayed"]
    report_id: str
    report_digest: str


type MeasurementReportErrorCode = Literal[
    "invalid_request",
    "unauthenticated",
    "forbidden",
    "unavailable",
    "overloaded",
    "report_conflict",
    "source_unavailable",
    "outside_retention",
    "capacity_reached",
]


class MeasurementReportError(ResponseModel):
    ok: Literal[False] = False
    error: MeasurementReportErrorCode
