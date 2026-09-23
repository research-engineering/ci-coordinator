"""Transport and role admission; cutover policy remains in its capability owners."""

from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.contracts import ErrorBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    ProductionCutoverRouteDependencies,
)
from ci_coordinator.api.http.production_cutover_contracts import (
    ProductionActivateBody,
    ProductionCommandBody,
    ProductionCutoverErrorBody,
    ProductionCutoverResultBody,
    ProductionScopeStateBody,
    ProductionStageBody,
)
from ci_coordinator.api.http.request_admission import RequestAdmissionPolicy
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.production_cutover import (
    ProductionAdministrationError,
    ProductionAdministrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import ControlPlaneRole, RoleAdmissionGranted
from ci_coordinator.kernel import hash_object
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverRejected,
    production_stage_input_digest,
)
from ci_coordinator.production_admission.limits import MAX_PRODUCTION_STAGE_BYTES

PRODUCTION_STAGE_PATH = "/api/v1/production/stages"
PRODUCTION_BEGIN_PATH = "/api/v1/production/cutovers"
PRODUCTION_ACTIVATE_PATH = "/api/v1/production/activations"
PRODUCTION_STATE_PATH = "/api/v1/production/scopes/{installation_id}/{repository_id}"
PRODUCTION_EVIDENCE_PATH = PRODUCTION_STATE_PATH + "/evidence/{authority_id}"
_NO_STORE = {"Cache-Control": "no-store"}

type _Bearer = Annotated[HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)]
type _Session = Annotated[str | None, Security(CONTROL_PLANE_SESSION)]
type _ScopeId = Annotated[int, Path(gt=0, le=9_007_199_254_740_991)]
type _AuthorityId = Annotated[str, Path(pattern=r"^production_admission_[0-9a-f]{32}$")]

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **{code: {"model": ProductionCutoverErrorBody} for code in (400, 403, 404, 409, 413, 503)},
    401: {"model": ProductionCutoverErrorBody, "headers": WWW_AUTHENTICATE_OPENAPI},
    422: {"model": ErrorBody | ProductionCutoverErrorBody},
}
PRODUCTION_BODY_LIMITS = tuple(
    BodyLimitPolicy(
        path=path,
        maximum_body_bytes=MAX_PRODUCTION_STAGE_BYTES
        if path == PRODUCTION_STAGE_PATH
        else 128 * 1024,
        invalid_content_length_response={"ok": False, "error": "invalid_evidence"},
        too_large_response={"ok": False, "error": "invalid_evidence"},
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
    )
    for path in (PRODUCTION_STAGE_PATH, PRODUCTION_BEGIN_PATH, PRODUCTION_ACTIVATE_PATH)
)
PRODUCTION_REQUEST_LIMITS = tuple(
    RequestAdmissionPolicy(
        path=path,
        methods=frozenset(
            {"GET" if path in {PRODUCTION_STATE_PATH, PRODUCTION_EVIDENCE_PATH} else "POST"}
        ),
        overload_response={"ok": False, "error": "overloaded"},
        timeout_response={"ok": False, "error": "unavailable"},
        concurrency_limit=1 if path == PRODUCTION_STAGE_PATH else 4,
        admission_key="production_stage" if path == PRODUCTION_STAGE_PATH else "production_control",
        response_headers=((b"cache-control", b"no-store"),),
    )
    for path in (
        PRODUCTION_STAGE_PATH,
        PRODUCTION_BEGIN_PATH,
        PRODUCTION_ACTIVATE_PATH,
        PRODUCTION_STATE_PATH,
        PRODUCTION_EVIDENCE_PATH,
    )
)


def build_production_cutover_router(dependencies: ProductionCutoverRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.post(
        PRODUCTION_STAGE_PATH,
        operation_id="stage_production_authority",
        response_model=ProductionCutoverResultBody,
        responses=_ERROR_RESPONSES,
    )
    async def stage(
        body: ProductionStageBody,
        request: Request,
        _security: _Bearer,
        _session: _Session = None,
    ) -> JSONResponse:
        actor = await _admit(request, dependencies, "configure", mutation=True)
        if isinstance(actor, JSONResponse):
            return actor
        envelope, evidence = body.envelope.encode("utf-8"), body.evidence.encode("utf-8")
        paths = tuple(body.provider_paths)
        result = await dependencies.use_case.stage(
            body.command("stage", actor, production_stage_input_digest(envelope, evidence, paths)),
            envelope=envelope,
            evidence=evidence,
            provider_paths=paths,
        )
        return _result(result)

    @router.post(
        PRODUCTION_BEGIN_PATH,
        operation_id="begin_production_cutover",
        response_model=ProductionCutoverResultBody,
        responses=_ERROR_RESPONSES,
    )
    async def begin(
        body: ProductionCommandBody,
        request: Request,
        _security: _Bearer,
        _session: _Session = None,
    ) -> JSONResponse:
        actor = await _admit(request, dependencies, "override", mutation=True)
        if isinstance(actor, JSONResponse):
            return actor
        return _result(
            await dependencies.use_case.begin(body.command("begin", actor, hash_object({})))
        )

    @router.post(
        PRODUCTION_ACTIVATE_PATH,
        operation_id="activate_production_authority",
        response_model=ProductionCutoverResultBody,
        responses=_ERROR_RESPONSES,
    )
    async def activate(
        body: ProductionActivateBody,
        request: Request,
        _security: _Bearer,
        _session: _Session = None,
    ) -> JSONResponse:
        actor = await _admit(request, dependencies, "activate", mutation=True)
        if isinstance(actor, JSONResponse):
            return actor
        content = body.drain_envelope.encode("utf-8")
        return _result(
            await dependencies.use_case.activate(
                body.command("activate", actor, sha256(content).hexdigest()),
                drain_envelope=content,
            )
        )

    @router.get(
        PRODUCTION_STATE_PATH,
        operation_id="inspect_production_authority",
        response_model=ProductionCutoverResultBody,
        responses=_ERROR_RESPONSES,
    )
    async def inspect(
        installation_id: _ScopeId,
        repository_id: _ScopeId,
        request: Request,
        _security: _Bearer,
        _session: _Session = None,
    ) -> JSONResponse:
        actor = await _admit(request, dependencies, "read", mutation=False)
        if isinstance(actor, JSONResponse):
            return actor
        state = await dependencies.use_case.inspect(
            actor=actor,
            scope=RepositoryScope(installation_id, repository_id),
        )
        return _result(
            state
            if isinstance(state, ProductionAdministrationError)
            else ProductionCutoverApplied(state)
        )

    @router.get(
        PRODUCTION_EVIDENCE_PATH,
        operation_id="read_production_evidence",
        response_class=Response,
        responses={**_ERROR_RESPONSES, 200: {"content": {"application/json": {}}}},
    )
    async def evidence(
        installation_id: _ScopeId,
        repository_id: _ScopeId,
        authority_id: _AuthorityId,
        request: Request,
        _security: _Bearer,
        _session: _Session = None,
    ) -> Response:
        actor = await _admit(request, dependencies, "read", mutation=False)
        if isinstance(actor, JSONResponse):
            return actor
        result = await dependencies.use_case.evidence(
            actor=actor,
            scope=RepositoryScope(installation_id, repository_id),
            authority_id=authority_id,
        )
        if isinstance(result, ProductionAdministrationError):
            return _result(result)
        return Response(content=result, media_type="application/json", headers=_NO_STORE)

    return router


async def _admit(
    request: Request,
    dependencies: ProductionCutoverRouteDependencies,
    role: ControlPlaneRole,
    *,
    mutation: bool,
) -> str | JSONResponse:
    admission = await authenticate_and_admit_roles(
        request,
        authenticator=dependencies.authenticator,
        role_admission=dependencies.role_admission,
        required_roles=frozenset({role}),
    )
    if isinstance(admission, InvalidCredential):
        return JSONResponse(
            {"ok": False, "error": "unauthenticated"},
            status_code=401,
            headers={**_NO_STORE, "WWW-Authenticate": "Bearer"},
        )
    if isinstance(admission, AuthenticationDependencyUnavailable):
        return _result(ProductionAdministrationError("unavailable"))
    if isinstance(admission, ForbiddenIdentity):
        return _result(ProductionAdministrationError("forbidden"))
    if not isinstance(admission, RoleAdmissionGranted):
        raise TypeError("production control-plane role admission is invalid")
    if mutation and not dependencies.mutation_admission.admits(request, admission.principal):
        return _result(ProductionAdministrationError("forbidden"))
    return admission.principal.actor_id


def _result(result: ProductionAdministrationResult) -> JSONResponse:
    if isinstance(result, ProductionCutoverApplied):
        body = ProductionCutoverResultBody(
            schema_version="ci-coordinator.production-cutover-result/v1",
            ok=True,
            state=ProductionScopeStateBody.from_state(result.state),
            duplicate=result.duplicate,
        )
        return JSONResponse(body.to_wire_mapping(), headers=_NO_STORE)
    if isinstance(result, ProductionCutoverRejected):
        status_code = 503 if result.reason == "capacity_exhausted" else 409
    else:
        status_code = {
            "forbidden": 403,
            "not_found": 404,
            "unavailable": 503,
            "invalid_evidence": 422,
        }[result.reason]
    body_error = ProductionCutoverErrorBody(ok=False, error=result.reason)
    return JSONResponse(body_error.to_wire_mapping(), status_code=status_code, headers=_NO_STORE)
