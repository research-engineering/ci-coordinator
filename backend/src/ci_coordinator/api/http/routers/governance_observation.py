"""Authenticated read-only effective-governance projection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    GovernanceObservationRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.governance_observation_contracts import (
    GovernanceObservationErrorCode,
    GovernanceObservationErrorResponse,
    GovernanceObservationResponse,
    governance_observation_projection,
)
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.governance_observation import (
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationUnavailable,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

GOVERNANCE_OBSERVATION_PATH = (
    "/api/v1/workbench/repositories/{installation_id}/{repository_id}/governance-observation"
)
GOVERNANCE_OBSERVATION_SUFFIX = "/governance-observation"
_NO_STORE = {"Cache-Control": "no-store"}


def build_governance_observation_router(
    dependencies: GovernanceObservationRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        GOVERNANCE_OBSERVATION_PATH,
        operation_id="observe_repository_effective_governance",
        response_model=GovernanceObservationResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": GovernanceObservationErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": GovernanceObservationErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": GovernanceObservationErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": GovernanceObservationErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": GovernanceObservationErrorResponse},
        },
    )
    async def observe_repository_effective_governance(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
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
        outcome = await dependencies.use_case(actor=admission.principal.actor_id, scope=scope)
        if isinstance(outcome, GovernanceObservationForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(outcome, GovernanceObservationUnavailable):
            return _unavailable(outcome)
        if not isinstance(outcome, GovernanceObservation):
            raise RuntimeError("unsupported governance observation outcome")
        response = GovernanceObservationResponse.model_validate(
            governance_observation_projection(outcome)
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=_NO_STORE,
        )

    return router


def _unavailable(outcome: GovernanceObservationUnavailable) -> JSONResponse:
    if outcome.reason == "not_found":
        return _error(status.HTTP_404_NOT_FOUND, "not_found")
    if outcome.reason == "rate_limited":
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            retry_after_seconds=outcome.retry_after_seconds,
        )
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, outcome.reason)


def _error(
    status_code: int,
    error: GovernanceObservationErrorCode,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    response = GovernanceObservationErrorResponse(
        ok=False,
        error=error,
        retry_after_seconds=retry_after_seconds,
    )
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
