from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceErrorResponse
from ci_coordinator.api.http.ci_observation_contracts import (
    ConfigureObservationBody,
    ObservationCursor,
    ObservationGapsResponse,
    ObservationMutationResponse,
    ObservationStatusResponse,
    ObservationWorkflowsResponse,
    observation_gaps_response,
    observation_status_response,
    observation_workflows_response,
)
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import control_plane_admission
from ci_coordinator.api.http.dependencies import (
    CiObservationRouteDependencies,
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
from ci_coordinator.app.ci_observation import ObservationCursorRejected
from ci_coordinator.ci_economics.observation_commands import (
    ObservationCommitted,
    ObservationConflict,
)
from ci_coordinator.ci_economics.observation_gaps import MAX_OBSERVATION_GAP_PAGE_SIZE
from ci_coordinator.ci_economics.observation_payload import (
    ObservationSnapshotPayload,
)
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.ci_economics.observation_workflows import (
    MAX_OBSERVATION_WORKFLOW_PAGES,
    ObservationWorkflowPage,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER, JsonResourceLimits

OBSERVATION_CONFIGURATION_PATH = "/api/v2/economics/observation"
OBSERVATION_STATUS_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/observation"
)
OBSERVATION_GAPS_PATH = OBSERVATION_STATUS_PATH + "/gaps"
OBSERVATION_WORKFLOWS_PATH = OBSERVATION_STATUS_PATH + "/workflows"
MAX_OBSERVATION_BODY_BYTES = 2_048
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
type ObservationError = Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"]

OBSERVATION_BODY_LIMIT = BodyLimitPolicy(
    path=OBSERVATION_CONFIGURATION_PATH,
    maximum_body_bytes=MAX_OBSERVATION_BODY_BYTES,
    invalid_content_length_response={"ok": False, "error": "invalid_request"},
    too_large_response={"ok": False, "error": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
OBSERVATION_REQUEST_LIMIT = RequestAdmissionPolicy(
    path=OBSERVATION_CONFIGURATION_PATH,
    methods=frozenset({"POST"}),
    concurrency_limit=2,
    admission_key="ci_observation_configuration",
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
    response_headers=((b"cache-control", b"no-store"),),
)


_ObservationJsonRoute = strict_json_route(
    methods=frozenset({"POST"}),
    maximum_bytes=MAX_OBSERVATION_BODY_BYTES,
    resource_limits=JsonResourceLimits(max_depth=4, max_nodes=80),
    invalid_response=lambda: _error(400, "invalid_request"),
)


def build_ci_observation_router(dependencies: CiObservationRouteDependencies) -> APIRouter:
    router = APIRouter(route_class=_ObservationJsonRoute)

    admit = control_plane_admission(dependencies, error=_error)

    @router.post(
        OBSERVATION_CONFIGURATION_PATH,
        operation_id="configure_ci_observation",
        response_model=ObservationMutationResponse,
        responses={**_ERRORS, 409: {"model": ObservationMutationResponse}},
    )
    async def configure(
        request: Request,
        body: ConfigureObservationBody,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "configure")
        if isinstance(admission, JSONResponse):
            return admission
        if request.query_params:
            return _error(400, "invalid_request")
        result = await dependencies.use_case.configure(
            body.to_command(admission.principal.actor_id)
        )
        if isinstance(result, ObservationCommitted):
            response = ObservationMutationResponse(
                operation_id=body.operation_id,
                outcome="replayed" if result.replayed else "committed",
                snapshot=ObservationSnapshotPayload.model_validate(
                    result.snapshot.canonical_mapping()
                ),
            )
            return JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        if isinstance(result, ObservationConflict):
            response = ObservationMutationResponse(
                operation_id=body.operation_id, outcome=result.reason, snapshot=None
            )
            return JSONResponse(response.to_wire_mapping(), status_code=409, headers=_NO_STORE)
        return _access_error(result)

    @router.get(
        OBSERVATION_STATUS_PATH,
        operation_id="get_ci_observation_status",
        response_model=ObservationStatusResponse,
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
        if isinstance(result, ObservationStatus):
            return JSONResponse(
                observation_status_response(result).to_wire_mapping(), headers=_NO_STORE
            )
        return _access_error(result)

    @router.get(
        OBSERVATION_GAPS_PATH,
        operation_id="list_ci_observation_gaps",
        response_model=ObservationGapsResponse,
        responses=_ERRORS,
    )
    async def gaps(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        after_cursor: Annotated[ObservationCursor | None, Query(alias="afterCursor")] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_OBSERVATION_GAP_PAGE_SIZE)] = 20,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        keys = tuple(key for key, _ in request.query_params.multi_items())
        if len(keys) != len(set(keys)) or set(keys) - {"afterCursor", "limit"}:
            return _error(400, "invalid_request")
        result = await dependencies.use_case.gaps(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            after_cursor=after_cursor,
            limit=limit,
        )
        if isinstance(result, ObservationGapPage):
            return JSONResponse(
                observation_gaps_response(result).to_wire_mapping(), headers=_NO_STORE
            )
        if isinstance(result, ObservationCursorRejected):
            return _error(400, "invalid_request")
        return _access_error(result)

    @router.get(
        OBSERVATION_WORKFLOWS_PATH,
        operation_id="list_ci_observation_workflows",
        response_model=ObservationWorkflowsResponse,
        responses=_ERRORS,
    )
    async def workflows(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        page_number: Annotated[
            int, Query(alias="pageNumber", ge=1, le=MAX_OBSERVATION_WORKFLOW_PAGES)
        ] = 1,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        keys = tuple(key for key, _ in request.query_params.multi_items())
        if len(keys) != len(set(keys)) or set(keys) - {"pageNumber"}:
            return _error(400, "invalid_request")
        result = await dependencies.use_case.workflows(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            page_number=page_number,
        )
        if isinstance(result, ObservationWorkflowPage):
            return JSONResponse(
                observation_workflows_response(result).to_wire_mapping(), headers=_NO_STORE
            )
        return _access_error(result)

    return router


def _access_error(result: object) -> JSONResponse:
    return (
        _error(403, "forbidden")
        if isinstance(result, CiEconomicsReadForbidden)
        else _error(503, "unavailable")
    )


def _error(code: int, error: ObservationError) -> JSONResponse:
    return JSONResponse(
        EconomicsSourceErrorResponse(ok=False, error=error).to_wire_mapping(),
        status_code=code,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if code == 401 else {})},
    )
