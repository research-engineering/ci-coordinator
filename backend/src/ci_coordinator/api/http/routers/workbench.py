"""Authenticated repository-scoped operator workbench snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import Field

from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    WorkbenchRouteDependencies,
)
from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.workbench_read_models import (
    MAX_WORKBENCH_SECTION_ITEMS,
    RepositoryWorkbenchSnapshot,
    WorkbenchForbidden,
    WorkbenchUnavailable,
)

WORKBENCH_REPOSITORY_PREFIX = "/api/v1/workbench/repositories"
WORKBENCH_REPOSITORY_PATH = WORKBENCH_REPOSITORY_PREFIX + "/{installation_id}/{repository_id}"
_NO_STORE = {"Cache-Control": "no-store"}


class RepositoryScopeResponse(ProjectedResponseModel):
    installation_id: int
    repository_id: int


class ShardedProfileCapacityResponse(ProjectedResponseModel):
    profile_id: str
    execution_kind: Literal["witness-shards"] = Field(alias="executionKind")
    shard_ids: tuple[str, ...]
    witness_count: int
    test_count: int
    max_parallel: int
    capacity_mode: Literal["optimized", "conservative"]
    capacity_reason: str | None


class NativeProfileExecutionResponse(ProjectedResponseModel):
    profile_id: str
    execution_kind: Literal["native-job-set"] = Field(alias="executionKind")
    job_id: str
    witness_count: int


type ProfileCapacityResponse = Annotated[
    ShardedProfileCapacityResponse | NativeProfileExecutionResponse,
    Field(discriminator="execution_kind"),
]


class PlanResponse(ProjectedResponseModel):
    record_id: str
    plan_id: str
    issued_at: datetime
    expires_at: datetime
    request_id: str
    event_name: str
    ref: str
    base_sha: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    execution_mode: Literal["selected", "full-ci"]
    verified_plan_id: str | None
    production_admission_receipt_id: str | None
    selected_obligation_ids: tuple[str, ...]
    omitted_obligation_ids: tuple[str, ...]
    selected_witness_ids: tuple[str, ...]
    test_manifest_id: str | None
    catalog_hash: str | None
    target_registry_hash: str | None
    profiles: tuple[ProfileCapacityResponse, ...]
    fallback_reason: str | None


class RunFindingResponse(ProjectedResponseModel):
    kind: str
    signal_id: str | None
    message: str


class RunResponse(ProjectedResponseModel):
    subject_id: str
    event_name: str
    ref: str
    base_sha: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    revision: int
    state: Literal["pending", "success", "failure", "conflict"]
    created_at: datetime
    deadline_at: datetime
    next_attempt_at: datetime
    attempt_count: int
    max_attempts: int
    claim_generation: int
    lease_active: bool
    lease_expires_at: datetime | None
    contract_hash: str
    findings: tuple[RunFindingResponse, ...]


class OverrideResponse(ProjectedResponseModel):
    override_id: str
    kind: Literal["force_full_ci", "disable_omission", "enable_omission"]
    subject_id: str | None
    operation_id: str
    actor: str
    reason: str
    applied_at: datetime
    expires_at: datetime | None
    active: bool
    audit_event_id: str


class ConfigEpochResponse(ProjectedResponseModel):
    epoch_id: str
    source_format: Literal["json", "yaml-1.2"]
    source_hash: str
    document_hash: str
    epoch_hash: str
    document_schema_id: str
    document_profile_id: str
    semantic_profile_id: str
    compiled_schema_id: str
    active: bool
    active_revision: int | None


class AuditEventResponse(ProjectedResponseModel):
    sequence: int
    audit_event_id: str
    subject_type: str
    subject_id: str
    event_type: str
    created_at: str
    actor: str
    payload: JsonValue
    payload_hash: str
    previous_event_hash: str | None
    event_hash: str


class ReplayResponse(ProjectedResponseModel):
    status: Literal["valid", "in_progress", "invalid", "unavailable"]
    snapshot_revision: int
    verified_revision: int | None
    reason: str | None


class TruncationResponse(ProjectedResponseModel):
    plans: bool
    runs: bool
    overrides: bool
    config_epochs: bool
    audit_events: bool


class WorkbenchSnapshotResponse(ResponseModel):
    ok: Literal[True]
    scope: RepositoryScopeResponse
    observed_at: datetime
    ledger_revision: int
    plans: tuple[PlanResponse, ...]
    runs: tuple[RunResponse, ...]
    overrides: tuple[OverrideResponse, ...]
    config_epochs: tuple[ConfigEpochResponse, ...]
    audit_events: tuple[AuditEventResponse, ...]
    replay: ReplayResponse
    truncated: TruncationResponse


class WorkbenchErrorResponse(ResponseModel):
    ok: Literal[False]
    error: Literal["unauthenticated", "forbidden", "unavailable"]


def build_workbench_router(dependencies: WorkbenchRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.get(
        WORKBENCH_REPOSITORY_PATH,
        operation_id="get_repository_workbench_snapshot",
        response_model=WorkbenchSnapshotResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": WorkbenchErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": WorkbenchErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": WorkbenchErrorResponse},
        },
    )
    async def get_repository_workbench_snapshot(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        limit: Annotated[int, Query(ge=1, le=MAX_WORKBENCH_SECTION_ITEMS)] = 10,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await authenticate_and_admit_roles(
            request,
            authenticator=dependencies.authenticator,
            role_admission=dependencies.role_admission,
            required_roles=frozenset({"read"}),
        )
        if isinstance(admission, InvalidCredential):
            return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if isinstance(admission, ForbiddenIdentity):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane role admission")
        scope = RepositoryScope(installation_id, repository_id)
        outcome = await dependencies.use_case(
            actor=admission.principal.actor_id,
            scope=scope,
            limit=limit,
        )
        if isinstance(outcome, WorkbenchForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(outcome, WorkbenchUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(outcome, RepositoryWorkbenchSnapshot):
            raise RuntimeError("unsupported workbench outcome")
        data = outcome.data
        response = WorkbenchSnapshotResponse.model_validate(
            {
                "ok": True,
                "scope": data.scope,
                "observed_at": data.observed_at,
                "ledger_revision": data.ledger_revision,
                "plans": data.plans,
                "runs": data.runs,
                "overrides": data.overrides,
                "config_epochs": data.config_epochs,
                "audit_events": data.audit_events,
                "replay": outcome.replay,
                "truncated": data.truncated,
            }
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=_NO_STORE,
        )

    return router


def _error(
    status_code: int,
    error: Literal["unauthenticated", "forbidden", "unavailable"],
) -> JSONResponse:
    response = WorkbenchErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == 401:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
