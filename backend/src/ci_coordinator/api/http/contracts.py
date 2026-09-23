"""Shared HTTP transport DTOs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel


class PlanRequestBody(RequestModel):
    schema_version: Literal["dynamic-ci-plan-request/v2"] = Field(
        validation_alias="schemaVersion", serialization_alias="schemaVersion"
    )
    request_id: str = Field(validation_alias="requestId", serialization_alias="requestId")
    installation_id: int = Field(
        validation_alias="installationId", serialization_alias="installationId"
    )
    repository_id: int = Field(validation_alias="repositoryId", serialization_alias="repositoryId")
    owner: str
    repository: str
    event_name: Literal["pull_request", "push", "merge_group"] = Field(
        validation_alias="eventName", serialization_alias="eventName"
    )
    ref: str
    base_sha: str = Field(validation_alias="baseSha", serialization_alias="baseSha")
    head_sha: str = Field(validation_alias="headSha", serialization_alias="headSha")
    execution_sha: str = Field(validation_alias="executionSha", serialization_alias="executionSha")
    workflow_run_id: int = Field(
        validation_alias="workflowRunId", serialization_alias="workflowRunId"
    )
    run_attempt: int = Field(validation_alias="runAttempt", serialization_alias="runAttempt")
    pull_request_number: int | None = Field(
        default=None, validation_alias="pullRequestNumber", serialization_alias="pullRequestNumber"
    )
    merge_group_head_ref: str | None = Field(
        default=None, validation_alias="mergeGroupHeadRef", serialization_alias="mergeGroupHeadRef"
    )


class SignedPlanEnvelopeBody(ResponseModel):
    schema_version: str
    key_id: str
    algorithm: str
    issued_at: str
    expires_at: str
    payload: dict[str, JsonValue]
    signature: str


class ErrorBody(ResponseModel):
    code: str


class InvalidRequestBody(ResponseModel):
    code: Literal["invalid_request"]
