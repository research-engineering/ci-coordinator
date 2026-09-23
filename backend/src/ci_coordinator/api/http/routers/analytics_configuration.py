from typing import Annotated, Literal

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.analytics_configuration_contracts import (
    PurposeConfigurationRequest,
    PurposeSettingsReadResponse,
    PurposeSettingsWriteResponse,
    settings_query,
)
from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceErrorResponse
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import control_plane_admission
from ci_coordinator.api.http.dependencies import (
    PurposeSettingsRouteDependencies,
)
from ci_coordinator.api.http.request_admission import RequestAdmissionPolicy
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.api.http.strict_json_route import strict_json_route
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden
from ci_coordinator.ci_economics.analytics_configuration import (
    MAX_PURPOSE_BYTES,
    PurposeSettingsConflict,
    PurposeSettingsSaved,
    PurposeSettingsSnapshot,
)
from ci_coordinator.ci_economics.archive_analytics_models import AnalyticsUnavailable
from ci_coordinator.kernel.canonical_json import JsonResourceLimits

PURPOSE_SETTINGS_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/history/analytics/settings"
)
_NO_STORE = {"Cache-Control": "no-store"}
_ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": EconomicsSourceErrorResponse},
    401: {"model": EconomicsSourceErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI},
    403: {"model": EconomicsSourceErrorResponse},
    422: {"model": InvalidRequestBody},
    503: {"model": EconomicsSourceErrorResponse},
}
type Bearer = Annotated[HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)]
type Session = Annotated[str | None, Security(CONTROL_PLANE_SESSION)]
PURPOSE_BODY_LIMIT = BodyLimitPolicy(
    path=PURPOSE_SETTINGS_PATH,
    methods=frozenset({"PUT"}),
    maximum_body_bytes=MAX_PURPOSE_BYTES,
    invalid_content_length_response={"ok": False, "error": "invalid_request"},
    too_large_response={"ok": False, "error": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
PURPOSE_REQUEST_LIMITS = tuple(
    RequestAdmissionPolicy(
        path=PURPOSE_SETTINGS_PATH,
        methods=frozenset({method}),
        concurrency_limit=2,
        admission_key="analytics_purpose_settings",
        timeout_seconds=20,
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
        response_headers=((b"cache-control", b"no-store"),),
    )
    for method in ("GET", "PUT")
)


_PurposeJsonRoute = strict_json_route(
    methods=frozenset({"PUT"}),
    maximum_bytes=MAX_PURPOSE_BYTES,
    resource_limits=JsonResourceLimits(max_depth=8, max_nodes=2048),
    invalid_response=lambda: _error(400, "invalid_request"),
)


def build_purpose_settings_router(dependencies: PurposeSettingsRouteDependencies) -> APIRouter:
    router = APIRouter(route_class=_PurposeJsonRoute)

    admit = control_plane_admission(dependencies, error=_error)

    @router.get(
        PURPOSE_SETTINGS_PATH,
        operation_id="get_analytics_purpose_settings",
        response_model=PurposeSettingsReadResponse,
        responses=_ERRORS,
        openapi_extra={
            "parameters": [
                {
                    "name": "generation",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
                }
            ]
        },
    )
    async def read(
        request: Request,
        installation_id: str,
        repository_id: str,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        if len(request.scope.get("query_string", b"")) > 256 or [
            key for key, _ in request.query_params.multi_items()
        ] != ["generation"]:
            return _error(400, "invalid_request")
        try:
            query = settings_query(
                installation_id, repository_id, request.query_params["generation"]
            )
        except (TypeError, ValueError):
            return _error(400, "invalid_request")
        result = await dependencies.use_case.read(actor=admission.principal.actor_id, query=query)
        if isinstance(result, PurposeSettingsSnapshot):
            if (result.scope, result.generation) != (query.scope, query.generation):
                return _error(503, "unavailable")
            response = PurposeSettingsReadResponse(
                outcome="available", snapshot=result, unavailable=None
            )
        elif isinstance(result, AnalyticsUnavailable):
            response = PurposeSettingsReadResponse(
                outcome="unavailable", snapshot=None, unavailable=result
            )
        else:
            return (
                _error(403, "forbidden")
                if isinstance(result, CiEconomicsReadForbidden)
                else _error(503, "unavailable")
            )
        return _bounded(response.to_wire_mapping())

    @router.put(
        PURPOSE_SETTINGS_PATH,
        operation_id="configure_analytics_purpose_settings",
        response_model=PurposeSettingsWriteResponse,
        responses={
            **_ERRORS,
            409: {"model": PurposeSettingsWriteResponse},
            413: {"model": EconomicsSourceErrorResponse},
        },
    )
    async def configure(
        request: Request,
        installation_id: str,
        repository_id: str,
        body: PurposeConfigurationRequest,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "configure")
        if isinstance(admission, JSONResponse):
            return admission
        try:
            query = settings_query(installation_id, repository_id, str(body.generation))
            command = body.command(admission.principal.actor_id)
            if request.query_params or query.scope != command.scope:
                raise ValueError("purpose settings path/body mismatch")
        except (TypeError, ValueError):
            return _error(400, "invalid_request")
        result = await dependencies.use_case.configure(command)
        if isinstance(result, PurposeSettingsSaved):
            if result.snapshot != command.successor() or type(result.replayed) is not bool:
                return _error(503, "unavailable")
            response = PurposeSettingsWriteResponse(
                operation_id=body.operation_id,
                outcome="replayed" if result.replayed else "committed",
                snapshot=result.snapshot,
            )
            return _bounded(response.to_wire_mapping())
        if isinstance(result, PurposeSettingsConflict):
            response = PurposeSettingsWriteResponse(
                operation_id=body.operation_id, outcome=result.reason, snapshot=None
            )
            return _bounded(response.to_wire_mapping(), 409)
        return (
            _error(403, "forbidden")
            if isinstance(result, CiEconomicsReadForbidden)
            else _error(503, "unavailable")
        )

    return router


def _bounded(value: object, status: int = 200) -> JSONResponse:
    response = JSONResponse(value, status_code=status, headers=_NO_STORE)
    return (
        response if len(response.body) <= MAX_PURPOSE_BYTES + 4096 else _error(503, "unavailable")
    )


def _error(
    status: int, error: Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"]
) -> JSONResponse:
    return JSONResponse(
        EconomicsSourceErrorResponse(ok=False, error=error).to_wire_mapping(),
        status_code=status,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if status == 401 else {})},
    )
