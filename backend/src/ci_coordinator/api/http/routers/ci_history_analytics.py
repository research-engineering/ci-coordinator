from typing import Annotated, Literal

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceErrorResponse
from ci_coordinator.api.http.ci_history_analytics_contracts import (
    HistoryAnalyticsResponse,
    analytics_openapi_parameters,
    parse_analytics_query,
)
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiHistoryAnalyticsRouteDependencies,
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
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsReport,
    AnalyticsUnavailable,
)

HISTORY_ANALYTICS_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/history/analytics"
)
MAX_ANALYTICS_RESPONSE_BYTES = 1024 * 1024
HISTORY_ANALYTICS_REQUEST_LIMIT = RequestAdmissionPolicy(
    path=HISTORY_ANALYTICS_PATH,
    methods=frozenset({"GET"}),
    concurrency_limit=2,
    admission_key="ci_history_analytics",
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
    response_headers=((b"cache-control", b"no-store"),),
)
_NO_STORE = {"Cache-Control": "no-store"}


def build_ci_history_analytics_router(
    dependencies: CiHistoryAnalyticsRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        HISTORY_ANALYTICS_PATH,
        operation_id="get_ci_history_analytics",
        response_model=HistoryAnalyticsResponse,
        openapi_extra={"parameters": analytics_openapi_parameters()},
        responses={
            400: {"model": EconomicsSourceErrorResponse},
            401: {"model": EconomicsSourceErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI},
            403: {"model": EconomicsSourceErrorResponse},
            503: {"model": EconomicsSourceErrorResponse},
        },
    )
    async def read(
        request: Request,
        installation_id: str,
        repository_id: str,
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
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
            return _error(401, "unauthenticated")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return _error(503, "unavailable")
        if isinstance(admission, ForbiddenIdentity):
            return _error(403, "forbidden")
        if len(request.scope.get("query_string", b"")) > 8192:
            return _error(400, "invalid_request")
        parameters = request.query_params
        if len(parameters.multi_items()) != len(parameters):
            return _error(400, "invalid_request")
        try:
            query = parse_analytics_query(installation_id, repository_id, dict(parameters))
        except (ValueError, TypeError):
            return _error(400, "invalid_request")
        result = await dependencies.use_case.read(actor=admission.principal.actor_id, query=query)
        if isinstance(result, AnalyticsReport):
            if result.query != query:
                return _error(503, "unavailable")
            response = HistoryAnalyticsResponse(
                outcome="available", report=result, unavailable=None
            )
        elif isinstance(result, AnalyticsUnavailable):
            response = HistoryAnalyticsResponse(
                outcome="unavailable", report=None, unavailable=result
            )
        else:
            return (
                _error(403, "forbidden")
                if isinstance(result, CiEconomicsReadForbidden)
                else (_error(503, "unavailable"))
            )
        encoded = JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        return (
            encoded
            if len(encoded.body) <= MAX_ANALYTICS_RESPONSE_BYTES
            else (_error(503, "unavailable"))
        )

    return router


def _error(
    status: int, error: Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"]
) -> JSONResponse:
    return JSONResponse(
        EconomicsSourceErrorResponse(ok=False, error=error).to_wire_mapping(),
        status_code=status,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if status == 401 else {})},
    )
