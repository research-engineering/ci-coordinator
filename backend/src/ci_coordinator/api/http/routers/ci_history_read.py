from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_history_read_contracts import (
    HistoryAttemptDetailResponse,
    HistoryReadErrorResponse,
    HistoryReadResponse,
    HistoryRetentionPreviewResponse,
    HistoryRetentionResultResponse,
)
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiHistoryReadRouteDependencies,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.api.http.request_admission import RequestAdmissionPolicy
from ci_coordinator.api.http.routers.ci_history import Bearer, ScopeId, Session
from ci_coordinator.api.http.security import WWW_AUTHENTICATE_HEADER, WWW_AUTHENTICATE_OPENAPI
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden
from ci_coordinator.app.ci_history_read import HistoryReadResult
from ci_coordinator.ci_economics.history_read import (
    HistoryReadKind,
    HistoryReadQuery,
    HistoryReadRejected,
)
from ci_coordinator.ci_economics.history_read_cursor import MAX_HISTORY_CURSOR_CHARS
from ci_coordinator.ci_economics.history_retention_commands import (
    ApplyHistoryRetention,
    HistoryRetentionPreview,
    HistoryRetentionRequest,
    HistoryRetentionResult,
    HistoryRetentionSelection,
)
from ci_coordinator.control_plane_identity import ControlPlaneRole, RoleAdmissionGranted
from ci_coordinator.kernel import StrictJsonError, load_strict_json
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER, JsonResourceLimits

HISTORY_READ_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/history/archive/{kind}"
)
HISTORY_ATTEMPT_DETAIL_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}"
    "/history/attempts/{workflow_run_id}/{run_attempt}/detail"
)
HISTORY_RETENTION_PREVIEW_PATH = "/api/v2/economics/history/retention/preview"
HISTORY_RETENTION_APPLY_PATH = "/api/v2/economics/history/retention/apply"
MAX_RETENTION_BODY_BYTES = 16_384
_NO_STORE = {"Cache-Control": "no-store"}
_ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": HistoryReadErrorResponse} for code in (400, 403, 404, 409, 413, 422, 503)
}
_ERRORS[401] = {"model": HistoryReadErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI}
type QueryId = Annotated[int, Query(ge=1, le=MAX_SAFE_JSON_INTEGER)]


def history_read_request_limit(concurrency_limit: int) -> RequestAdmissionPolicy:
    return RequestAdmissionPolicy(
        path=HISTORY_READ_PATH,
        methods=frozenset({"GET"}),
        concurrency_limit=concurrency_limit,
        admission_key="ci_economics_query",
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
        response_headers=((b"cache-control", b"no-store"),),
    )


def history_attempt_detail_request_limit(concurrency_limit: int) -> RequestAdmissionPolicy:
    return RequestAdmissionPolicy(
        path=HISTORY_ATTEMPT_DETAIL_PATH,
        methods=frozenset({"GET"}),
        concurrency_limit=concurrency_limit,
        admission_key="ci_economics_query",
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
        response_headers=((b"cache-control", b"no-store"),),
    )


HISTORY_RETENTION_BODY_LIMITS = tuple(
    BodyLimitPolicy(
        path=path,
        maximum_body_bytes=MAX_RETENTION_BODY_BYTES,
        invalid_content_length_response={"ok": False, "error": "invalid_request"},
        too_large_response={"ok": False, "error": "invalid_request"},
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    )
    for path in (HISTORY_RETENTION_PREVIEW_PATH, HISTORY_RETENTION_APPLY_PATH)
)
HISTORY_RETENTION_REQUEST_LIMITS = tuple(
    RequestAdmissionPolicy(
        path=path,
        methods=frozenset({"POST"}),
        concurrency_limit=2,
        admission_key="ci_history_configuration",
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
        response_headers=((b"cache-control", b"no-store"),),
    )
    for path in (HISTORY_RETENTION_PREVIEW_PATH, HISTORY_RETENTION_APPLY_PATH)
)


class _HistoryReadRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        native = super().get_route_handler()

        async def admitted(request: Request) -> Response:
            if len(request.scope.get("query_string", b"")) > 16_384:
                return _error(400, "invalid_request")
            if len(request.query_params.multi_items()) != len(request.query_params):
                return _error(400, "invalid_request")
            if request.method == "POST":
                if request.query_params:
                    return _error(400, "invalid_request")
                headers = request.scope["headers"]
                if tuple(v for k, v in headers if k.lower() == b"content-type") != (
                    b"application/json",
                ) or any(k.lower() == b"content-encoding" for k, _ in headers):
                    return _error(400, "invalid_request")
                try:
                    load_strict_json(
                        await request.body(),
                        max_bytes=MAX_RETENTION_BODY_BYTES,
                        resource_limits=JsonResourceLimits(max_depth=8, max_nodes=1024),
                    )
                except StrictJsonError:
                    return _error(400, "invalid_request")
            try:
                response = await native(request)
            except RequestValidationError:
                return _error(422, "invalid_request")
            response.headers["Cache-Control"] = "no-store"
            return response

        return admitted


def build_ci_history_read_router(dependencies: CiHistoryReadRouteDependencies) -> APIRouter:
    router = APIRouter(route_class=_HistoryReadRoute)

    async def admit(
        request: Request, role: ControlPlaneRole, *, mutation: bool = False
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
        if isinstance(admission, ForbiddenIdentity) or (
            mutation and not dependencies.mutation_admission.admits(request, admission.principal)
        ):
            return _error(403, "forbidden")
        return admission

    @router.get(
        HISTORY_ATTEMPT_DETAIL_PATH,
        operation_id="read_ci_history_attempt_detail",
        response_model=HistoryAttemptDetailResponse,
        responses=_ERRORS,
    )
    async def detail(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        workflow_run_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        run_attempt: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        generation: QueryId,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        if set(request.query_params) != {"generation"}:
            return _error(400, "invalid_request")
        try:
            query = HistoryReadQuery(
                installationId=installation_id,
                repositoryId=repository_id,
                generation=generation,
                kind="detail",
                limit=1,
                workflowRunId=workflow_run_id,
                runAttempt=run_attempt,
            )
        except ValueError:
            return _error(422, "invalid_request")
        result = await dependencies.use_case.read(
            actor=admission.principal.actor_id, query=query, cursor=None
        )
        if isinstance(result, HistoryReadResult):
            try:
                response = HistoryAttemptDetailResponse.from_result(result)
            except ValueError:
                return _error(503, "unavailable")
            return JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        return _failure(result)

    @router.get(
        HISTORY_READ_PATH,
        operation_id="read_ci_history_archive",
        response_model=HistoryReadResponse,
        responses=_ERRORS,
    )
    async def read(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        kind: HistoryReadKind,
        generation: QueryId,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        workflow_id: QueryId | None = None,
        workflow_run_id: QueryId | None = None,
        run_attempt: QueryId | None = None,
        created_from: Annotated[str | None, Query(max_length=64)] = None,
        created_through: Annotated[str | None, Query(max_length=64)] = None,
        job_name: Annotated[str | None, Query(max_length=512)] = None,
        cursor: Annotated[str | None, Query(max_length=MAX_HISTORY_CURSOR_CHARS)] = None,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        if set(request.query_params) - {
            "generation",
            "limit",
            "workflow_id",
            "workflow_run_id",
            "run_attempt",
            "created_from",
            "created_through",
            "job_name",
            "cursor",
        }:
            return _error(400, "invalid_request")
        try:
            query = HistoryReadQuery(
                installationId=installation_id,
                repositoryId=repository_id,
                generation=generation,
                kind=kind,
                limit=limit,
                workflowId=workflow_id,
                workflowRunId=workflow_run_id,
                runAttempt=run_attempt,
                createdFrom=created_from,
                createdThrough=created_through,
                jobName=job_name,
            )
        except ValueError:
            return _error(422, "invalid_request")
        result = await dependencies.use_case.read(
            actor=admission.principal.actor_id, query=query, cursor=cursor
        )
        if isinstance(result, HistoryReadResult):
            return JSONResponse(
                HistoryReadResponse.from_result(result).to_wire_mapping(), headers=_NO_STORE
            )
        return _failure(result)

    @router.post(
        HISTORY_RETENTION_PREVIEW_PATH,
        operation_id="preview_ci_history_retention",
        response_model=HistoryRetentionPreviewResponse,
        responses=_ERRORS,
    )
    async def preview(
        request: Request,
        body: HistoryRetentionSelection,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "configure")
        if isinstance(admission, JSONResponse):
            return admission
        result = await dependencies.use_case.preview(
            actor=admission.principal.actor_id, selection=body
        )
        if isinstance(result, HistoryRetentionPreview):
            return JSONResponse(
                HistoryRetentionPreviewResponse(
                    preview=result, reviewed_digest=result.review_digest
                ).to_wire_mapping(),
                headers=_NO_STORE,
            )
        return _failure(result)

    @router.post(
        HISTORY_RETENTION_APPLY_PATH,
        operation_id="apply_ci_history_retention",
        response_model=HistoryRetentionResultResponse,
        responses={
            **_ERRORS,
            409: {"model": HistoryReadErrorResponse | HistoryRetentionResultResponse},
        },
    )
    async def apply(
        request: Request,
        body: HistoryRetentionRequest,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "configure", mutation=True)
        if isinstance(admission, JSONResponse):
            return admission
        result = await dependencies.use_case.apply(
            ApplyHistoryRetention.from_request(body, actor=admission.principal.actor_id)
        )
        if isinstance(result, HistoryRetentionResult):
            return JSONResponse(
                HistoryRetentionResultResponse.model_validate(result).to_wire_mapping(),
                headers=_NO_STORE,
                status_code=200 if result.outcome in {"committed", "replayed"} else 409,
            )
        return _failure(result)

    return router


def _failure(result: object) -> JSONResponse:
    if isinstance(result, HistoryReadRejected):
        code = (
            404
            if result.reason == "not_found"
            else 400
            if result.reason == "invalid_cursor"
            else 409
        )
        return _error(code, result.reason)
    return (
        _error(403, "forbidden")
        if isinstance(result, CiEconomicsReadForbidden)
        else _error(503, "unavailable")
    )


def _error(code: int, error: str) -> JSONResponse:
    return JSONResponse(
        HistoryReadErrorResponse.model_validate({"ok": False, "error": error}).to_wire_mapping(),
        status_code=code,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if code == 401 else {})},
    )
