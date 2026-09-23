from dataclasses import replace
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Request, Security
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceErrorResponse
from ci_coordinator.api.http.ci_history_contracts import (
    HistoryMutationResponse,
    HistoryStatusResponse,
    history_status_response,
)
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import control_plane_admission
from ci_coordinator.api.http.dependencies import (
    CiHistoryRouteDependencies,
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
from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationConflict,
    HistoryConfigurationInvalid,
    HistoryConfigurationRequest,
    HistoryConfigured,
)
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairRequest,
    HistoryGapRepairResult,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.history_payload import HistoryDatasetPayload
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER, JsonResourceLimits

HISTORY_CONFIGURATION_PATH = "/api/v2/economics/history"
HISTORY_GAP_REPAIR_PATH = "/api/v2/economics/history/gaps/retry"
HISTORY_STATUS_PATH = "/api/v2/economics/repositories/{installation_id}/{repository_id}/history"
MAX_HISTORY_BODY_BYTES = 4096
_NO_STORE = {"Cache-Control": "no-store"}
_ERRORS: dict[int | str, dict[str, object]] = {
    400: {"model": EconomicsSourceErrorResponse},
    403: {"model": EconomicsSourceErrorResponse},
    413: {"model": EconomicsSourceErrorResponse},
    503: {"model": EconomicsSourceErrorResponse},
    401: {"model": EconomicsSourceErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI},
    422: {"model": InvalidRequestBody},
}
type ScopeId = Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)]
type Bearer = Annotated[HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)]
type Session = Annotated[str | None, Security(CONTROL_PLANE_SESSION)]
type HistoryError = Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"]

HISTORY_BODY_LIMIT = BodyLimitPolicy(
    path=HISTORY_CONFIGURATION_PATH,
    maximum_body_bytes=MAX_HISTORY_BODY_BYTES,
    invalid_content_length_response={"ok": False, "error": "invalid_request"},
    too_large_response={"ok": False, "error": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
HISTORY_REQUEST_LIMIT = RequestAdmissionPolicy(
    path=HISTORY_CONFIGURATION_PATH,
    methods=frozenset({"POST"}),
    concurrency_limit=2,
    admission_key="ci_history_configuration",
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
    response_headers=((b"cache-control", b"no-store"),),
)
HISTORY_GAP_REPAIR_BODY_LIMIT = replace(HISTORY_BODY_LIMIT, path=HISTORY_GAP_REPAIR_PATH)
HISTORY_GAP_REPAIR_REQUEST_LIMIT = replace(HISTORY_REQUEST_LIMIT, path=HISTORY_GAP_REPAIR_PATH)


_HistoryJsonRoute = strict_json_route(
    methods=frozenset({"POST"}),
    maximum_bytes=MAX_HISTORY_BODY_BYTES,
    resource_limits=JsonResourceLimits(max_depth=6, max_nodes=256),
    invalid_response=lambda: _error(400, "invalid_request"),
)


def build_ci_history_router(dependencies: CiHistoryRouteDependencies) -> APIRouter:
    router = APIRouter(route_class=_HistoryJsonRoute)

    admit = control_plane_admission(dependencies, error=_error)

    @router.post(
        HISTORY_GAP_REPAIR_PATH,
        operation_id="retry_ci_history_gaps",
        response_model=HistoryGapRepairResult,
        responses={**_ERRORS, 409: {"model": HistoryGapRepairResult}},
    )
    async def repair(
        request: Request,
        body: HistoryGapRepairRequest,
        response: Response,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> HistoryGapRepairResult | JSONResponse:
        admission = await admit(request, "configure")
        if isinstance(admission, JSONResponse):
            return admission
        if request.query_params:
            return _error(400, "invalid_request")
        result = await dependencies.use_case.repair(
            RepairHistoryGaps.from_request(body, actor=admission.principal.actor_id)
        )
        if isinstance(result, HistoryGapRepairResult):
            response.status_code = 200 if result.outcome in {"committed", "replayed"} else 409
            response.headers.update(_NO_STORE)
            return result
        return _access_error(result)

    @router.post(
        HISTORY_CONFIGURATION_PATH,
        operation_id="configure_ci_history",
        response_model=HistoryMutationResponse,
        responses={
            **_ERRORS,
            400: {"model": EconomicsSourceErrorResponse | HistoryMutationResponse},
            409: {"model": HistoryMutationResponse},
        },
    )
    async def configure(
        request: Request,
        body: HistoryConfigurationRequest,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "configure")
        if isinstance(admission, JSONResponse):
            return admission
        if request.query_params:
            return _error(400, "invalid_request")
        command = ConfigureHistory.from_request(body, actor=admission.principal.actor_id)
        result = await dependencies.use_case.configure(command)
        if isinstance(result, HistoryConfigured):
            response = HistoryMutationResponse(
                operation_id=body.operation_id,
                outcome="replayed" if result.replayed else "committed",
                snapshot=HistoryDatasetPayload.model_validate(result.snapshot.canonical_mapping()),
            )
            return JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        if isinstance(result, HistoryConfigurationConflict | HistoryConfigurationInvalid):
            response = HistoryMutationResponse(
                operation_id=body.operation_id, outcome=result.reason, snapshot=None
            )
            return JSONResponse(
                response.to_wire_mapping(),
                status_code=409 if isinstance(result, HistoryConfigurationConflict) else 400,
                headers=_NO_STORE,
            )
        return _access_error(result)

    @router.get(
        HISTORY_STATUS_PATH,
        operation_id="get_ci_history_status",
        response_model=HistoryStatusResponse,
        responses=_ERRORS,
    )
    async def status(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        if request.query_params:
            return _error(400, "invalid_request")
        result = await dependencies.use_case.status(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
        )
        if isinstance(result, HistoryStatus):
            return JSONResponse(
                history_status_response(result).to_wire_mapping(), headers=_NO_STORE
            )
        return _access_error(result)

    return router


def _access_error(result: object) -> JSONResponse:
    return (
        _error(403, "forbidden")
        if isinstance(result, CiEconomicsReadForbidden)
        else _error(503, "unavailable")
    )


def _error(code: int, error: HistoryError) -> JSONResponse:
    return JSONResponse(
        EconomicsSourceErrorResponse(ok=False, error=error).to_wire_mapping(),
        status_code=code,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if code == 401 else {})},
    )
