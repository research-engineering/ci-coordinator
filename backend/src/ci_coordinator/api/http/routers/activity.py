from collections.abc import Callable, Coroutine
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.activity_contracts import (
    ActivityContextResponse,
    ActivityErrorResponse,
    ActivityItemResponse,
    ActivityPageResponse,
    ActivityParameters,
)
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    ActivityRouteDependencies as ActivityRouteDependencies,
)
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
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
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity.activity import ActivityForbidden, ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_query import (
    ActivityQuery,
    parse_activity_time,
)
from ci_coordinator.control_plane_identity.model import (
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
)

type ScopeId = Annotated[int, Path(ge=1, le=9007199254740991)]
type Bearer = Annotated[HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)]
type Session = Annotated[str | None, Security(CONTROL_PLANE_SESSION)]
_NO_STORE = {"Cache-Control": "no-store"}
ACTIVITY_PATHS = (
    "/api/v1/activity/security",
    "/api/v1/activity/security/export",
    "/api/v1/activity/repositories/{installation_id}/{repository_id}",
    "/api/v1/activity/repositories/{installation_id}/{repository_id}/export",
)
ACTIVITY_REQUEST_LIMITS = tuple(
    RequestAdmissionPolicy(
        path=path,
        methods=frozenset({"GET"}),
        overload_response={"ok": False, "error": "unavailable"},
        timeout_response={"ok": False, "error": "unavailable"},
        concurrency_limit=4,
        admission_key="administrator_activity",
        response_headers=((b"cache-control", b"no-store"),),
        timeout_seconds=5,
    )
    for path in ACTIVITY_PATHS
)


class _ActivityRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        native = super().get_route_handler()

        async def admitted(request: Request) -> Response:
            if len(request.scope.get("query_string", b"")) > 8192:
                return _error("invalid_request", 400)
            if len(request.query_params.multi_items()) != len(request.query_params):
                return _error("invalid_request", 400)
            try:
                return await native(request)
            except RequestValidationError:
                return _error("invalid_request", 400)

        return admitted


def build_activity_router(dependencies: ActivityRouteDependencies) -> APIRouter:
    router = APIRouter(
        route_class=_ActivityRoute,
        responses={
            **{code: {"model": ActivityErrorResponse} for code in (400, 403, 503)},
            401: {"model": ActivityErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI},
        },
    )

    async def page(
        request: Request, parameters: ActivityParameters, scope: RepositoryScope | None
    ) -> JSONResponse:
        admitted = await authenticate_and_admit_roles(
            request,
            authenticator=dependencies.authenticator,
            role_admission=dependencies.role_admission,
            required_roles=frozenset({"audit"}),
        )
        if isinstance(admitted, InvalidCredential):
            return _error("unauthenticated", 401)
        if isinstance(admitted, AuthenticationDependencyUnavailable):
            return _error("unavailable", 503)
        if isinstance(admitted, ForbiddenIdentity):
            return _error("forbidden", 403)
        if not isinstance(admitted.principal, KeycloakHumanPrincipal | KeycloakWorkloadPrincipal):
            return _error("forbidden", 403)
        try:
            query = ActivityQuery(
                source="security" if scope is None else "business",
                since=parse_activity_time(parameters.since),
                until=parse_activity_time(parameters.until),
                issuer=(
                    admitted.principal.issuer
                    if scope is None and parameters.issuer is None
                    else parameters.issuer
                ),
                scope=scope,
                actor=parameters.actor,
                action=parameters.action,
                limit=int(parameters.limit),
            )
            exporting = request.url.path.endswith("/export")
            result = await dependencies.service.page(
                query,
                admitted.principal,
                at=dependencies.clock.now(),
                cursor=parameters.cursor,
                export=exporting,
            )
        except ValueError:
            return _error("invalid_request", 400)
        except ActivityUnavailable:
            return _error("unavailable", 503)
        if isinstance(result, ActivityForbidden):
            return _error("forbidden", 403)
        response = ActivityPageResponse(
            context=ActivityContextResponse(
                source=query.source,
                issuer=query.issuer,
                installation_id=None if scope is None else scope.installation_id,
                repository_id=None if scope is None else scope.repository_id,
            ),
            items=tuple(ActivityItemResponse.model_validate(item) for item in result.items),
            next_cursor=result.next_cursor,
            observed_at=result.observed_at,
            retention_seconds=result.retention_seconds,
            integrity=result.integrity,
        )
        headers = {
            **_NO_STORE,
            **(
                {"Content-Disposition": 'attachment; filename="activity.json"'} if exporting else {}
            ),
        }
        encoded = JSONResponse(response.to_wire_mapping(), headers=headers)
        return encoded if len(encoded.body) <= 1048576 else _error("unavailable", 503)

    @router.get(
        "/api/v1/activity/security",
        response_model=ActivityPageResponse,
        operation_id="query_security_activity",
    )
    @router.get(
        "/api/v1/activity/security/export",
        response_model=ActivityPageResponse,
        operation_id="export_security_activity",
    )
    async def security(
        request: Request,
        parameters: Annotated[ActivityParameters, Query()],
        _bearer: Bearer = None,
        _session: Session = None,
    ) -> JSONResponse:
        return await page(request, parameters, None)

    @router.get(
        "/api/v1/activity/repositories/{installation_id}/{repository_id}",
        response_model=ActivityPageResponse,
        operation_id="query_repository_activity",
    )
    @router.get(
        "/api/v1/activity/repositories/{installation_id}/{repository_id}/export",
        response_model=ActivityPageResponse,
        operation_id="export_repository_activity",
    )
    async def repository(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        parameters: Annotated[ActivityParameters, Query()],
        _bearer: Bearer = None,
        _session: Session = None,
    ) -> JSONResponse:
        return await page(request, parameters, RepositoryScope(installation_id, repository_id))

    return router


def _error(
    error: Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"], status: int
) -> JSONResponse:
    return JSONResponse(
        ActivityErrorResponse(error=error).to_wire_mapping(),
        status_code=status,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if status == 401 else {})},
    )
