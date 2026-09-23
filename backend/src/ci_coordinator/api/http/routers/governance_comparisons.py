"""Control-plane read of exact baseline-to-observation governance comparison."""

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
    GovernanceComparisonRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.governance_comparison_contracts import (
    GovernanceComparisonErrorResponse,
    GovernanceComparisonResponse,
    governance_comparison_error_response,
    governance_comparison_response,
)
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.governance_comparison import (
    GovernanceComparisonEvidence,
    GovernanceComparisonForbidden,
    GovernanceComparisonStale,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.governance_observation import GovernanceObservationUnavailable
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

GOVERNANCE_COMPARISON_PATH = (
    "/api/v1/workbench/repositories/{installation_id}/{repository_id}/governance-comparison"
)
GOVERNANCE_COMPARISON_SUFFIX = "/governance-comparison"


def build_governance_comparison_router(
    dependencies: GovernanceComparisonRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        GOVERNANCE_COMPARISON_PATH,
        operation_id="compare_repository_effective_governance",
        response_model=GovernanceComparisonResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": GovernanceComparisonErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": GovernanceComparisonErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": GovernanceComparisonErrorResponse},
            status.HTTP_409_CONFLICT: {"model": GovernanceComparisonErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": GovernanceComparisonErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": GovernanceComparisonErrorResponse},
        },
    )
    async def compare_repository_effective_governance(
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
            return governance_comparison_error_response(
                status.HTTP_401_UNAUTHORIZED,
                "unauthenticated",
            )
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return governance_comparison_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "unavailable",
            )
        if isinstance(admission, ForbiddenIdentity):
            return governance_comparison_error_response(
                status.HTTP_403_FORBIDDEN,
                "forbidden",
            )
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane comparison admission")
        outcome = await dependencies.use_case(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
        )
        if isinstance(outcome, GovernanceComparisonEvidence):
            return governance_comparison_response(outcome)
        if isinstance(outcome, GovernanceComparisonForbidden):
            return governance_comparison_error_response(
                status.HTTP_403_FORBIDDEN,
                "forbidden",
            )
        if isinstance(outcome, GovernanceComparisonStale):
            return governance_comparison_error_response(
                status.HTTP_409_CONFLICT,
                "stale",
            )
        if isinstance(outcome, GovernanceObservationUnavailable):
            return _unavailable(outcome)
        raise RuntimeError("unsupported governance comparison outcome")

    return router


def _unavailable(outcome: GovernanceObservationUnavailable) -> JSONResponse:
    if outcome.reason == "not_found":
        return governance_comparison_error_response(status.HTTP_404_NOT_FOUND, "not_found")
    if outcome.reason == "rate_limited":
        return governance_comparison_error_response(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            retry_after_seconds=outcome.retry_after_seconds,
        )
    return governance_comparison_error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        outcome.reason,
    )
