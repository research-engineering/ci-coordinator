"""Control-plane reads and approval of owner-governed expected governance state."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    GovernanceBaselineRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.governance_baseline_contracts import (
    GovernanceBaselineApprovalRequest,
    GovernanceBaselineApprovalResponse,
    GovernanceBaselineErrorResponse,
    GovernanceBaselineReadResponse,
    governance_baseline_approval_response,
    governance_baseline_error_response,
    governance_baseline_read_response,
)
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import ControlPlaneRole, RoleAdmissionGranted
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselinePointer,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

GOVERNANCE_BASELINES_PATH = (
    "/api/v1/workbench/repositories/{installation_id}/{repository_id}/governance-baselines"
)
GOVERNANCE_BASELINES_SUFFIX = "/governance-baselines"
MAX_GOVERNANCE_BASELINE_BODY_BYTES = 4_096
GOVERNANCE_BASELINE_BODY_LIMIT = BodyLimitPolicy(
    path=GOVERNANCE_BASELINES_PATH,
    maximum_body_bytes=MAX_GOVERNANCE_BASELINE_BODY_BYTES,
    invalid_content_length_response={"code": "invalid_request"},
    too_large_response={"code": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)


def build_governance_baseline_router(
    dependencies: GovernanceBaselineRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        GOVERNANCE_BASELINES_PATH,
        operation_id="read_active_governance_baseline",
        response_model=GovernanceBaselineReadResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": GovernanceBaselineErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": GovernanceBaselineErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": GovernanceBaselineErrorResponse},
        },
    )
    async def read_active_governance_baseline(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies, roles=frozenset({"read"}))
        rejected = _admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane baseline admission")
        scope = RepositoryScope(installation_id, repository_id)
        outcome = await dependencies.use_case.read_active(
            actor=admission.principal.actor_id,
            scope=scope,
        )
        if outcome.state in {"active", "absent"}:
            return governance_baseline_read_response(outcome, scope)
        return _outcome_error(outcome.state)

    @router.post(
        GOVERNANCE_BASELINES_PATH,
        operation_id="approve_governance_baseline",
        response_model=GovernanceBaselineApprovalResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            status.HTTP_200_OK: {"model": GovernanceBaselineApprovalResponse},
            status.HTTP_400_BAD_REQUEST: {"model": InvalidRequestBody},
            status.HTTP_401_UNAUTHORIZED: {
                "model": GovernanceBaselineErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": GovernanceBaselineErrorResponse},
            status.HTTP_409_CONFLICT: {"model": GovernanceBaselineErrorResponse},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": InvalidRequestBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": GovernanceBaselineErrorResponse},
        },
    )
    async def approve_governance_baseline(
        request: Request,
        body: GovernanceBaselineApprovalRequest,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(
            request,
            dependencies,
            roles=frozenset({"configure"}),
            mutation=True,
        )
        rejected = _admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane baseline admission")
        scope = RepositoryScope(installation_id, repository_id)
        expected = body.expected_active
        command = GovernanceBaselineCommand(
            scope=scope,
            operation_id=body.operation_id,
            expected_state_digest=body.expected_state_digest,
            expected_active=(
                None
                if expected is None
                else GovernanceBaselinePointer(
                    scope,
                    expected.baseline_id,
                    expected.version,
                    expected.state_digest,
                )
            ),
            actor=admission.principal.actor_id,
            reason=body.reason,
        )
        outcome = await dependencies.use_case.accept(command)
        if outcome.state in {"accepted", "duplicate", "unchanged"}:
            return governance_baseline_approval_response(
                outcome,
                command=command,
            )
        return _outcome_error(outcome.state)

    return router


type _Admission = (
    RoleAdmissionGranted
    | InvalidCredential
    | ForbiddenIdentity
    | AuthenticationDependencyUnavailable
)


async def _admit(
    request: Request,
    dependencies: GovernanceBaselineRouteDependencies,
    *,
    roles: frozenset[ControlPlaneRole],
    mutation: bool = False,
) -> _Admission:
    admission = await authenticate_and_admit_roles(
        request,
        authenticator=dependencies.authenticator,
        role_admission=dependencies.role_admission,
        required_roles=roles,
    )
    if (
        mutation
        and isinstance(admission, RoleAdmissionGranted)
        and not dependencies.mutation_admission.admits(request, admission.principal)
    ):
        return ForbiddenIdentity()
    return admission


def _admission_error(admission: _Admission) -> JSONResponse | None:
    if isinstance(admission, InvalidCredential):
        return governance_baseline_error_response(
            status.HTTP_401_UNAUTHORIZED,
            "unauthenticated",
        )
    if isinstance(admission, ForbiddenIdentity):
        return governance_baseline_error_response(status.HTTP_403_FORBIDDEN, "forbidden")
    if isinstance(admission, AuthenticationDependencyUnavailable):
        return governance_baseline_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "unavailable",
        )
    return None


def _outcome_error(state_value: object) -> JSONResponse:
    if state_value == "forbidden":
        return governance_baseline_error_response(status.HTTP_403_FORBIDDEN, "forbidden")
    if state_value in {"stale", "baseline_conflict", "operation_conflict"}:
        return governance_baseline_error_response(status.HTTP_409_CONFLICT, state_value)
    if state_value == "unavailable":
        return governance_baseline_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "unavailable",
        )
    raise RuntimeError("unsupported governance baseline outcome")
