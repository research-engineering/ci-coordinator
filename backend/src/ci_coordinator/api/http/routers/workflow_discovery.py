"""Authenticated exact-repository workflow-discovery route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    WorkflowDiscoveryRouteDependencies,
)
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.api.http.workflow_discovery_contracts import (
    WorkflowDiscoveryErrorCode,
    WorkflowDiscoveryErrorResponse,
    WorkflowDiscoveryResponse,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.workflow_discovery import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryInvalidRequest,
    WorkflowDiscoveryUnavailable,
)

WORKFLOW_DISCOVERY_PATH = (
    "/api/v1/workbench/repositories/{installation_id}/{repository_id}/workflow-discovery"
)
_NO_STORE = {"Cache-Control": "no-store"}


def build_workflow_discovery_router(
    dependencies: WorkflowDiscoveryRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        WORKFLOW_DISCOVERY_PATH,
        operation_id="discover_repository_workflows",
        response_model=WorkflowDiscoveryResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": WorkflowDiscoveryErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": WorkflowDiscoveryErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": WorkflowDiscoveryErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {
                "model": InvalidRequestBody | WorkflowDiscoveryErrorResponse
            },
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": WorkflowDiscoveryErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": WorkflowDiscoveryErrorResponse},
        },
    )
    async def discover_repository_workflows(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        revision: Annotated[str | None, Query(pattern=r"^[0-9a-f]{40}$")] = None,
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
            revision=revision,
        )
        if isinstance(outcome, WorkflowDiscoveryForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(outcome, WorkflowDiscoveryInvalidRequest):
            return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_revision")
        if isinstance(outcome, WorkflowDiscoveryUnavailable):
            return _unavailable(outcome)
        if not isinstance(outcome, WorkflowDiscoveryCompleted):
            raise RuntimeError("unsupported workflow discovery outcome")
        report = outcome.report
        proposal = outcome.proposal
        response = WorkflowDiscoveryResponse.model_validate(
            {
                "ok": True,
                "repository": report.repository,
                "revision": report.revision,
                "parser_version": report.parser_version,
                "complete": report.complete,
                "local_graph_closed": report.local_graph_closed,
                "inventory_digest": report.inventory_digest,
                "sources": report.sources,
                "workflows": report.workflows,
                "call_edges": report.call_edges,
                "facts": report.facts,
                "unknowns": report.unknowns,
                "non_claims": report.non_claims,
                "target_projection": {
                    "status": outcome.target_projection.status,
                    "registry_hash": outcome.target_projection.registry_hash,
                },
                "adoption_assessments": outcome.adoption_assessments,
                "proposal": {
                    "manifest_id": proposal.manifest_id,
                    "state": proposal.state,
                    "generator_version": proposal.generator_version,
                    "inventory_digest": proposal.inventory_digest,
                    "selected_workflow_path": proposal.selected_workflow_path,
                    "selected_job_id": proposal.selected_job_id,
                    "selected_job_name": proposal.selected_job_name,
                    "selected_events": proposal.selected_events,
                    "policy_source": (
                        None
                        if proposal.policy_source is None
                        else proposal.policy_source.decode("utf-8", errors="strict")
                    ),
                    "admitted_epoch_id": (
                        None if proposal.admission is None else proposal.admission.epoch_id
                    ),
                    "diagnostics": proposal.diagnostics,
                    "blockers": proposal.blockers,
                    "unknown_ids": proposal.unknown_ids,
                    "non_claims": proposal.non_claims,
                },
            }
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=_NO_STORE,
        )

    return router


def _unavailable(outcome: WorkflowDiscoveryUnavailable) -> JSONResponse:
    if outcome.reason == "not_found":
        return _error(status.HTTP_404_NOT_FOUND, "not_found")
    if outcome.reason == "rate_limited":
        return _error(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited")
    if outcome.reason == "overloaded":
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "overloaded")
    admitted: set[WorkflowDiscoveryErrorCode] = {
        "malformed_provider_response",
        "provider_binding_mismatch",
        "report_limit_exceeded",
        "source_limit_exceeded",
        "source_tree_limit_exceeded",
        "unavailable",
    }
    error: WorkflowDiscoveryErrorCode = (
        outcome.reason if outcome.reason in admitted else "unavailable"
    )
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, error)


def _error(status_code: int, error: WorkflowDiscoveryErrorCode) -> JSONResponse:
    response = WorkflowDiscoveryErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == 401:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
