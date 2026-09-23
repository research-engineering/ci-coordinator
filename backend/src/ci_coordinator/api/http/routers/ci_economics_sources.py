from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_economics_source_contracts import (
    EconomicsSourceDiscoveryBody,
    EconomicsSourceDiscoveryResponse,
    EconomicsSourceErrorCode,
    EconomicsSourceErrorResponse,
    EconomicsSourceRegistrationBody,
    EconomicsSourceRegistrationResponse,
    discovery_response,
    provider_source_response,
)
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiEconomicsSourceRouteDependencies,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.api.http.request_admission import RequestAdmissionPolicy
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.api.http.strict_json_route import strict_json_route
from ci_coordinator.app.ci_economics_sources import (
    CiEconomicsSourceForbidden,
    CiEconomicsSourceUnavailable,
    ProviderSourceRegistrationAvailable,
)
from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.control_plane_identity import ControlPlaneRole, RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import JsonResourceLimits

SOURCE_DISCOVERY_PATH: Final = "/api/v2/economics/source-discovery"
SOURCE_REGISTRATION_PATH: Final = "/api/v2/economics/sources"
MAX_SOURCE_BODY_BYTES: Final = 2_048
_NO_STORE = {"Cache-Control": "no-store"}

SOURCE_BODY_LIMITS = tuple(
    BodyLimitPolicy(
        path=path,
        maximum_body_bytes=MAX_SOURCE_BODY_BYTES,
        invalid_content_length_response={"ok": False, "error": "invalid_request"},
        too_large_response={"ok": False, "error": "invalid_request"},
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    )
    for path in (SOURCE_DISCOVERY_PATH, SOURCE_REGISTRATION_PATH)
)
SOURCE_REQUEST_LIMITS = tuple(
    RequestAdmissionPolicy(
        path=path,
        methods=frozenset({"POST"}),
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
        concurrency_limit=2,
        admission_key="ci_economics_sources",
        response_headers=((b"cache-control", b"no-store"),),
    )
    for path in (SOURCE_DISCOVERY_PATH, SOURCE_REGISTRATION_PATH)
)


_SourceJsonRoute = strict_json_route(
    methods=None,
    maximum_bytes=MAX_SOURCE_BODY_BYTES,
    resource_limits=JsonResourceLimits(max_depth=1, max_nodes=6),
    invalid_response=lambda: _error(400, "invalid_request"),
)


def build_ci_economics_source_router(dependencies: CiEconomicsSourceRouteDependencies) -> APIRouter:
    router = APIRouter(route_class=_SourceJsonRoute)

    @router.post(
        SOURCE_DISCOVERY_PATH,
        operation_id="discover_ci_economics_sources",
        response_model=EconomicsSourceDiscoveryResponse,
        responses=_responses(),
    )
    async def discover_sources(
        body: EconomicsSourceDiscoveryBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        result = await dependencies.use_case.discover_page(
            actor=admission.principal.actor_id,
            scope=body.scope,
            window=body.window,
            page_number=body.page_number,
        )
        if isinstance(result, ProviderRunDiscoveryPage):
            return JSONResponse(discovery_response(result).to_wire_mapping(), headers=_NO_STORE)
        return _source_error(result)

    @router.post(
        SOURCE_REGISTRATION_PATH,
        operation_id="register_ci_economics_source",
        status_code=201,
        response_model=EconomicsSourceRegistrationResponse,
        responses={
            **_responses(),
            200: {"model": EconomicsSourceRegistrationResponse},
            409: {"model": EconomicsSourceRegistrationResponse},
        },
    )
    async def register_source(
        body: EconomicsSourceRegistrationBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies, "configure")
        if isinstance(admission, JSONResponse):
            return admission
        result = await dependencies.use_case.register_attempt(
            actor=admission.principal.actor_id,
            scope=body.scope,
            workflow_run_id=body.workflow_run_id,
            run_attempt=body.run_attempt,
        )
        if isinstance(result, ProviderSourceRegistrationAvailable):
            code = {"registered": 201, "replayed": 200}.get(result.outcome, 409)
            response = EconomicsSourceRegistrationResponse(
                schema_version="ci-economics-source-registration/v2",
                source=provider_source_response(result.source),
                outcome=result.outcome,
            )
            return JSONResponse(response.to_wire_mapping(), status_code=code, headers=_NO_STORE)
        return _source_error(result)

    return router


async def _admit(
    request: Request,
    dependencies: CiEconomicsSourceRouteDependencies,
    role: ControlPlaneRole,
) -> RoleAdmissionGranted | JSONResponse:
    admission = await authenticate_and_admit_roles(
        request,
        authenticator=dependencies.authenticator,
        role_admission=dependencies.role_admission,
        required_roles=frozenset({role}),
    )
    if isinstance(admission, InvalidCredential):
        return _error(401, "unauthenticated")
    if isinstance(admission, AuthenticationDependencyUnavailable):
        return _error(503, "unavailable")
    if isinstance(admission, ForbiddenIdentity):
        return _error(403, "forbidden")
    if not dependencies.mutation_admission.admits(request, admission.principal):
        return _error(403, "forbidden")
    return admission


def _source_error(
    result: ProviderAttemptDeferred | CiEconomicsSourceForbidden | CiEconomicsSourceUnavailable,
) -> JSONResponse:
    if isinstance(result, CiEconomicsSourceForbidden):
        return _error(403, "forbidden")
    if isinstance(result, ProviderAttemptDeferred):
        return _error(503, result.reason)
    return _error(503, "unavailable")


def _error(code: int, error: EconomicsSourceErrorCode) -> JSONResponse:
    return JSONResponse(
        EconomicsSourceErrorResponse(ok=False, error=error).to_wire_mapping(),
        status_code=code,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if code == 401 else {})},
    )


def _responses() -> dict[int | str, dict[str, object]]:
    return {
        400: {"model": EconomicsSourceErrorResponse},
        401: {"model": EconomicsSourceErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI},
        403: {"model": EconomicsSourceErrorResponse},
        413: {"model": EconomicsSourceErrorResponse},
        422: {"model": InvalidRequestBody},
        503: {"model": EconomicsSourceErrorResponse},
    }
