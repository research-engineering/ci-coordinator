"""Authenticated read-only provider installation and repository catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    ProviderInventoryRouteDependencies,
)
from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.provider_inventory import (
    InstallationCatalog,
    ProviderInventoryForbidden,
    ProviderInventoryIneligible,
    ProviderInventoryUnavailable,
    RepositoryPage,
)
from ci_coordinator.provider_inventory.model import (
    InstallationState,
    InventoryFailureReason,
)

PROVIDER_INSTALLATIONS_PATH = "/api/v1/workbench/installations"
PROVIDER_REPOSITORIES_PATH = PROVIDER_INSTALLATIONS_PATH + "/{installation_id}/repositories"
_NO_STORE = {"Cache-Control": "no-store"}


class InstallationResponse(ProjectedResponseModel):
    installation_id: int
    account_id: int
    account_login: str
    account_type: Literal["Organization", "User"]
    repository_selection: Literal["all", "selected"]
    state: InstallationState


class InstallationFailureResponse(ProjectedResponseModel):
    installation_id: int
    reason: InventoryFailureReason
    retry_after_seconds: int | None


class InstallationCatalogResponse(ResponseModel):
    ok: Literal[True]
    observed_at: datetime
    complete: bool
    installations: tuple[InstallationResponse, ...]
    failures: tuple[InstallationFailureResponse, ...]
    page: int
    per_page: int
    has_next_page: bool
    consistency: Literal["best_effort"]


class RepositoryScopeResponse(ProjectedResponseModel):
    installation_id: int
    repository_id: int


class RepositoryResponse(ProjectedResponseModel):
    scope: RepositoryScopeResponse
    node_id: str
    owner_id: int
    owner_login: str
    name: str
    full_name: str
    visibility: Literal["public", "private", "internal"]
    default_branch: str
    archived: bool
    disabled: bool
    fork: bool
    workbench_authorized: bool


class RepositoryPageResponse(ResponseModel):
    ok: Literal[True]
    installation: InstallationResponse
    observed_at: datetime
    page: int
    per_page: int
    total_count: int
    has_next_page: bool
    consistency: Literal["best_effort"]
    repositories: tuple[RepositoryResponse, ...]


type InventoryErrorCode = Literal[
    "unauthenticated",
    "forbidden",
    "unavailable",
    "rate_limited",
    "not_found",
    "malformed_provider_response",
    "provider_binding_mismatch",
    "suspended",
    "unsupported_account_type",
]


class ProviderInventoryErrorResponse(ResponseModel):
    ok: Literal[False]
    error: InventoryErrorCode
    retry_after_seconds: int | None


def build_provider_inventory_router(
    dependencies: ProviderInventoryRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        PROVIDER_INSTALLATIONS_PATH,
        operation_id="list_operator_provider_installations",
        response_model=InstallationCatalogResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": ProviderInventoryErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ProviderInventoryErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ProviderInventoryErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": ProviderInventoryErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ProviderInventoryErrorResponse},
        },
    )
    async def list_operator_provider_installations(
        request: Request,
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        per_page: Annotated[int, Query(alias="perPage", ge=1, le=100)] = 30,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit_read(request, dependencies)
        if isinstance(admission, InvalidCredential):
            return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if isinstance(admission, ForbiddenIdentity):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane role admission")
        outcome = await dependencies.use_case.list_installations(
            actor=admission.principal.actor_id,
            page=page,
            per_page=per_page,
        )
        if isinstance(outcome, ProviderInventoryForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(outcome, ProviderInventoryUnavailable):
            return _provider_error(outcome)
        if not isinstance(outcome, InstallationCatalog):
            raise RuntimeError("unsupported provider installation catalog outcome")
        response = InstallationCatalogResponse.model_validate(
            {
                "ok": True,
                "observed_at": outcome.observed_at,
                "complete": outcome.complete,
                "installations": outcome.installations,
                "failures": outcome.failures,
                "page": outcome.page,
                "per_page": outcome.per_page,
                "has_next_page": outcome.has_next_page,
                "consistency": outcome.consistency,
            }
        )
        return _json(response)

    @router.get(
        PROVIDER_REPOSITORIES_PATH,
        operation_id="list_operator_provider_repositories",
        response_model=RepositoryPageResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {
                "model": ProviderInventoryErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ProviderInventoryErrorResponse},
            status.HTTP_404_NOT_FOUND: {"model": ProviderInventoryErrorResponse},
            status.HTTP_409_CONFLICT: {"model": ProviderInventoryErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": ProviderInventoryErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ProviderInventoryErrorResponse},
        },
    )
    async def list_operator_provider_repositories(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        page: Annotated[int, Query(ge=1, le=10_000)] = 1,
        per_page: Annotated[int, Query(alias="perPage", ge=1, le=100)] = 100,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit_read(request, dependencies)
        if isinstance(admission, InvalidCredential):
            return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if isinstance(admission, ForbiddenIdentity):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("unsupported control-plane role admission")
        outcome = await dependencies.use_case.list_repositories(
            actor=admission.principal.actor_id,
            installation_id=installation_id,
            page=page,
            per_page=per_page,
        )
        if isinstance(outcome, ProviderInventoryForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(outcome, ProviderInventoryIneligible):
            return _error(status.HTTP_409_CONFLICT, outcome.reason)
        if isinstance(outcome, ProviderInventoryUnavailable):
            return _provider_error(outcome)
        if not isinstance(outcome, RepositoryPage):
            raise RuntimeError("unsupported provider repository page outcome")
        response = RepositoryPageResponse.model_validate(
            {
                "ok": True,
                "installation": outcome.installation,
                "observed_at": outcome.observed_at,
                "page": outcome.page,
                "per_page": outcome.per_page,
                "total_count": outcome.total_count,
                "has_next_page": outcome.has_next_page,
                "consistency": outcome.consistency,
                "repositories": outcome.repositories,
            }
        )
        return _json(response)

    return router


async def _admit_read(
    request: Request,
    dependencies: ProviderInventoryRouteDependencies,
) -> (
    RoleAdmissionGranted
    | InvalidCredential
    | ForbiddenIdentity
    | AuthenticationDependencyUnavailable
):
    return await authenticate_and_admit_roles(
        request,
        authenticator=dependencies.authenticator,
        role_admission=dependencies.role_admission,
        required_roles=frozenset({"read"}),
    )


def _provider_error(outcome: ProviderInventoryUnavailable) -> JSONResponse:
    if outcome.reason == "rate_limited":
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            outcome.reason,
            retry_after_seconds=outcome.retry_after_seconds,
        )
    if outcome.reason == "not_found":
        return _error(status.HTTP_404_NOT_FOUND, outcome.reason)
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, outcome.reason)


def _error(
    status_code: int,
    error: InventoryErrorCode,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    response = ProviderInventoryErrorResponse.model_validate(
        {
            "ok": False,
            "error": error,
            "retry_after_seconds": retry_after_seconds,
        }
    )
    headers = dict(_NO_STORE)
    if status_code == 401:
        headers.update(WWW_AUTHENTICATE_HEADER)
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )


def _json(response: ResponseModel) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response.to_wire_mapping(),
        headers=_NO_STORE,
    )
