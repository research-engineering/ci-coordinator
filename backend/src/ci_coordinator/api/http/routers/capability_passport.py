"""Authenticated read-only capability passport for one repository scope."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CapabilityPassportRouteDependencies,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.api.http.routers.provider_inventory import InstallationResponse
from ci_coordinator.api.http.routers.workbench import WORKBENCH_REPOSITORY_PATH
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.capability_passport import (
    CapabilityNextAction,
    CapabilityPassport,
    CapabilityPassportForbidden,
    InstallationGrantReason,
    InstallationGrantStatus,
    ProviderLimitReason,
    ProviderLimitStatus,
    ReconciliationReason,
    ReconciliationStatus,
    TrustKeyReason,
    TrustKeyStatus,
    UnprojectedFact,
    UnprojectedFactReason,
    UnprojectedFactSource,
)
from ci_coordinator.ci_economics.observation_progress import ObservationOutcome
from ci_coordinator.ci_economics.observation_scan import ObservationLane
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.observability import BackgroundHealthState

CAPABILITY_PASSPORT_PATH = WORKBENCH_REPOSITORY_PATH + "/capabilities"
_NO_STORE = {"Cache-Control": "no-store"}
type CapabilityPassportError = Literal["unauthenticated", "forbidden", "unavailable"]


class CapabilityScopeResponse(ResponseModel):
    installation_id: int
    repository_id: int


class InstallationGrantResponse(ResponseModel):
    status: InstallationGrantStatus
    source: Literal["github_app_installation"]
    observed_at: datetime
    reason: InstallationGrantReason | None
    installation: InstallationResponse | None


class ProviderLimitResponse(ResponseModel):
    status: ProviderLimitStatus
    source: Literal["github_app_installation"]
    observed_at: datetime
    reason: ProviderLimitReason
    retry_after_seconds: int | None


class UnprojectedFactResponse(ResponseModel):
    status: Literal["unknown"]
    source: UnprojectedFactSource
    reason: UnprojectedFactReason


class ReconciliationLaneResponse(ResponseModel):
    lane: ObservationLane
    next_attempt_at: datetime
    last_completed_through: datetime | None
    last_page_at: datetime | None
    last_outcome: ObservationOutcome | None


class ReconciliationResponse(ResponseModel):
    status: ReconciliationStatus
    source: Literal["ci_observation_status"]
    observed_at: datetime | None
    reason: ReconciliationReason | None
    enabled: bool | None
    lanes: tuple[ReconciliationLaneResponse, ...]
    gaps_recorded: bool | None
    detail_truncated_until: datetime | None
    background_state: BackgroundHealthState


class TrustKeyResponse(ResponseModel):
    status: TrustKeyStatus
    source: Literal["github_actions_jwks_cache"]
    observed_at: datetime
    reason: TrustKeyReason | None
    key_count: int
    age_seconds: int | None


class CapabilityPassportResponse(ResponseModel):
    ok: Literal[True]
    schema_version: Literal["capability-passport/v1"]
    scope: CapabilityScopeResponse
    observed_at: datetime
    next_action: CapabilityNextAction
    installation_grant: InstallationGrantResponse
    provider_limits: ProviderLimitResponse
    webhook_observation: UnprojectedFactResponse
    reconciliation: ReconciliationResponse
    actions_oidc_keys: TrustKeyResponse
    production_admission_keys: UnprojectedFactResponse


class CapabilityPassportErrorResponse(ResponseModel):
    ok: Literal[False]
    error: CapabilityPassportError


def build_capability_passport_router(
    dependencies: CapabilityPassportRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        CAPABILITY_PASSPORT_PATH,
        operation_id="get_repository_capability_passport",
        response_model=CapabilityPassportResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": CapabilityPassportErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": CapabilityPassportErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": CapabilityPassportErrorResponse},
        },
    )
    async def get_repository_capability_passport(
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
            required_roles=frozenset({"audit"}),
        )
        if isinstance(admission, InvalidCredential):
            return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if isinstance(admission, ForbiddenIdentity):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane role admission")
        outcome = await dependencies.use_case(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
        )
        if isinstance(outcome, CapabilityPassportForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if not isinstance(outcome, CapabilityPassport):
            raise RuntimeError("unsupported capability passport outcome")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=capability_passport_response(outcome).to_wire_mapping(),
            headers=_NO_STORE,
        )

    return router


def capability_passport_response(passport: CapabilityPassport) -> CapabilityPassportResponse:
    grant = passport.installation_grant
    limits = passport.provider_limits
    reconciliation = passport.reconciliation
    observation = reconciliation.observation
    keys = passport.actions_oidc_keys
    cache = keys.cache
    return CapabilityPassportResponse(
        ok=True,
        schema_version="capability-passport/v1",
        scope=CapabilityScopeResponse(
            installation_id=passport.scope.installation_id,
            repository_id=passport.scope.repository_id,
        ),
        observed_at=passport.observed_at,
        next_action=passport.next_action,
        installation_grant=InstallationGrantResponse(
            status=grant.status,
            source="github_app_installation",
            observed_at=passport.observed_at,
            reason=grant.reason,
            installation=None
            if grant.installation is None
            else InstallationResponse.model_validate(grant.installation),
        ),
        provider_limits=ProviderLimitResponse(
            status=limits.status,
            source="github_app_installation",
            observed_at=passport.observed_at,
            reason=limits.reason,
            retry_after_seconds=limits.retry_after_seconds,
        ),
        webhook_observation=_unprojected(passport.webhook_observation),
        reconciliation=ReconciliationResponse(
            status=reconciliation.status,
            source="ci_observation_status",
            observed_at=None if observation is None else observation.observed_at,
            reason=reconciliation.reason,
            enabled=reconciliation.enabled,
            lanes=()
            if observation is None
            else tuple(
                ReconciliationLaneResponse(
                    lane=scan.state.lane,
                    next_attempt_at=scan.state.next_attempt_at,
                    last_completed_through=scan.last_completed_through,
                    last_page_at=scan.last_page_at,
                    last_outcome=scan.last_outcome,
                )
                for scan in observation.scans
            ),
            gaps_recorded=reconciliation.gaps_recorded,
            detail_truncated_until=None
            if observation is None
            else observation.detail_truncated_until,
            background_state=passport.background_reconciliation,
        ),
        actions_oidc_keys=TrustKeyResponse(
            status=keys.status,
            source="github_actions_jwks_cache",
            observed_at=passport.observed_at,
            reason=keys.reason,
            key_count=0 if cache is None else cache.key_count,
            age_seconds=None if cache is None else cache.age_seconds,
        ),
        production_admission_keys=_unprojected(passport.production_admission_keys),
    )


def _unprojected(fact: UnprojectedFact) -> UnprojectedFactResponse:
    return UnprojectedFactResponse(status="unknown", source=fact.source, reason=fact.reason)


def _error(status_code: int, error: CapabilityPassportError) -> JSONResponse:
    response = CapabilityPassportErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
