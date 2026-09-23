"""Exact public HTTP contracts for bounded CI economics reads."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.app.ci_economics import AttemptJobsAvailable
from ci_coordinator.ci_economics import (
    MAX_ECONOMICS_PAGE_SIZE,
    MAX_JOB_LABEL_CODE_POINTS,
    MAX_JOB_LABELS,
    MAX_JOB_NAME_CODE_POINTS,
    MAX_JOBS_PER_ATTEMPT,
    MAX_RUNNER_TEXT_CODE_POINTS,
    AttemptIdentity,
    AttemptSummaryPage,
    EvidenceQuality,
    PlannedRoute,
    WorkflowConclusion,
    decode_attempt_cursor,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_ATTEMPT_CURSOR_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\.[0-9a-f]{64}$"


def _canonical_attempt_cursor(value: str) -> str:
    decode_attempt_cursor(value)
    return value


type AttemptCursorQuery = Annotated[str, AfterValidator(_canonical_attempt_cursor)]


class CiEconomicsAttemptIdentityResponse(ResponseModel):
    installation_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    repository_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    workflow_run_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    run_attempt: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    head_sha: str = Field(min_length=40, max_length=40, pattern=_SHA1_PATTERN)


class CiEconomicsAttemptSummaryResponse(ResponseModel):
    subject_id: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    attempt: CiEconomicsAttemptIdentityResponse
    contract_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    planned_route: PlannedRoute
    job_count: int = Field(ge=1, le=MAX_JOBS_PER_ATTEMPT)
    snapshot_digest: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    recorded_at: datetime


class CiEconomicsAttemptPageResponse(ResponseModel):
    schema_version: Literal["ci-economics-attempt-page/v1"]
    ok: Literal[True]
    items: tuple[CiEconomicsAttemptSummaryResponse, ...] = Field(max_length=MAX_ECONOMICS_PAGE_SIZE)
    next_cursor: str | None = Field(
        default=None,
        min_length=92,
        max_length=92,
        pattern=_ATTEMPT_CURSOR_PATTERN,
    )


class CiEconomicsJobTimingResponse(ProjectedResponseModel):
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class CiEconomicsRunnerResponse(ProjectedResponseModel):
    runner_id: int | None = Field(default=None, ge=1, le=MAX_SAFE_JSON_INTEGER)
    runner_name: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNNER_TEXT_CODE_POINTS
    )
    runner_group_id: int | None = Field(default=None, ge=1, le=MAX_SAFE_JSON_INTEGER)
    runner_group_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_RUNNER_TEXT_CODE_POINTS,
    )


class CiEconomicsJobResponse(ProjectedResponseModel):
    provider_job_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    name: str = Field(min_length=1, max_length=MAX_JOB_NAME_CODE_POINTS)
    conclusion: WorkflowConclusion
    timing: CiEconomicsJobTimingResponse
    labels: tuple[
        Annotated[str, Field(min_length=1, max_length=MAX_JOB_LABEL_CODE_POINTS)], ...
    ] = Field(max_length=MAX_JOB_LABELS)
    runner: CiEconomicsRunnerResponse
    semantic_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)


class CiEconomicsDurationResponse(ProjectedResponseModel):
    quality: EvidenceQuality
    known_value_ms: int | None = Field(default=None, ge=0, le=MAX_SAFE_JSON_INTEGER)
    known_job_count: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    total_job_count: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)


class CiEconomicsAttemptJobsResponse(ResponseModel):
    schema_version: Literal["ci-economics-attempt-jobs/v1"]
    ok: Literal[True]
    subject_id: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    attempt: CiEconomicsAttemptIdentityResponse
    contract_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    planned_route: PlannedRoute
    recorded_at: datetime
    retain_until: datetime
    snapshot_digest: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    definition_version: Literal["ci-economics-measurement/v1"]
    observation_set_hash: str = Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN)
    queue: CiEconomicsDurationResponse
    runner_occupancy: CiEconomicsDurationResponse
    attempt_wall: CiEconomicsDurationResponse
    jobs: tuple[CiEconomicsJobResponse, ...] = Field(max_length=MAX_ECONOMICS_PAGE_SIZE)
    next_job_id: int | None = Field(default=None, ge=1, le=MAX_SAFE_JSON_INTEGER)


type CiEconomicsErrorCode = Literal[
    "unauthenticated",
    "forbidden",
    "not_found",
    "unavailable",
]


class CiEconomicsErrorResponse(ResponseModel):
    ok: Literal[False]
    error: CiEconomicsErrorCode


def attempt_page_projection(page: AttemptSummaryPage) -> dict[str, object]:
    if type(page) is not AttemptSummaryPage:
        raise TypeError("CI economics page projection requires an exact page")
    return {
        "schema_version": "ci-economics-attempt-page/v1",
        "ok": True,
        "items": tuple(
            {
                "subject_id": item.subject_id,
                "attempt": _attempt_projection(item.attempt),
                "contract_hash": item.contract_hash,
                "planned_route": item.planned_route,
                "job_count": item.job_count,
                "snapshot_digest": item.snapshot_digest,
                "recorded_at": item.recorded_at,
            }
            for item in page.items
        ),
        "next_cursor": page.next_cursor,
    }


def attempt_jobs_projection(result: AttemptJobsAvailable) -> dict[str, object]:
    if type(result) is not AttemptJobsAvailable:
        raise TypeError("CI economics jobs projection requires an exact result")
    economics = result.economics
    snapshot = economics.snapshot
    return {
        "schema_version": "ci-economics-attempt-jobs/v1",
        "ok": True,
        "subject_id": snapshot.subject_id,
        "attempt": _attempt_projection(snapshot.attempt),
        "contract_hash": snapshot.contract_hash,
        "planned_route": snapshot.planned_route,
        "recorded_at": snapshot.recorded_at,
        "retain_until": snapshot.retain_until,
        "snapshot_digest": snapshot.snapshot_digest,
        "definition_version": economics.definition_version,
        "observation_set_hash": economics.observation_set_hash,
        "queue": economics.queue,
        "runner_occupancy": economics.runner_occupancy,
        "attempt_wall": economics.attempt_wall,
        "jobs": result.jobs,
        "next_job_id": result.next_job_id,
    }


def _attempt_projection(attempt: AttemptIdentity) -> dict[str, object]:
    return {
        "installation_id": attempt.scope.installation_id,
        "repository_id": attempt.scope.repository_id,
        "workflow_run_id": attempt.workflow_run_id,
        "run_attempt": attempt.run_attempt,
        "head_sha": attempt.head_sha,
    }
