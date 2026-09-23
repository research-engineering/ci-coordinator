from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_economics_budget_contracts import (
    BudgetPoliciesResponse,
    BudgetPolicyMutationResponse,
    BudgetSignalsResponse,
    ConfigureBudgetPolicyBody,
    budget_signal_response,
)
from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceErrorResponse
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import control_plane_admission
from ci_coordinator.api.http.dependencies import (
    CiEconomicsBudgetRouteDependencies,
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
from ci_coordinator.ci_economics.budget import ReportBudgetOutcome
from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted, BudgetPolicyConflict
from ci_coordinator.ci_economics.budget_payload import BudgetDigest, BudgetKey, BudgetPolicyPayload
from ci_coordinator.ci_economics.budget_signal import BudgetSignalPage
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER, JsonResourceLimits

BUDGET_CONFIGURATION_PATH = "/api/v2/economics/budget-policies"
BUDGET_POLICIES_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/budget-policies"
)
BUDGET_SIGNALS_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/budget-signals"
)
MAX_BUDGET_BODY_BYTES = 2_048
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
type LiteralBudgetError = Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"]

BUDGET_BODY_LIMIT = BodyLimitPolicy(
    path=BUDGET_CONFIGURATION_PATH,
    maximum_body_bytes=MAX_BUDGET_BODY_BYTES,
    invalid_content_length_response={"ok": False, "error": "invalid_request"},
    too_large_response={"ok": False, "error": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
BUDGET_REQUEST_LIMIT = RequestAdmissionPolicy(
    path=BUDGET_CONFIGURATION_PATH,
    methods=frozenset({"POST"}),
    concurrency_limit=2,
    admission_key="ci_economics_budgets",
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
    response_headers=((b"cache-control", b"no-store"),),
)


_BudgetJsonRoute = strict_json_route(
    methods=frozenset({"POST"}),
    maximum_bytes=MAX_BUDGET_BODY_BYTES,
    resource_limits=JsonResourceLimits(max_depth=3, max_nodes=24),
    invalid_response=lambda: _error(400, "invalid_request"),
)


def build_ci_economics_budget_router(dependencies: CiEconomicsBudgetRouteDependencies) -> APIRouter:
    router = APIRouter(route_class=_BudgetJsonRoute)

    admit = control_plane_admission(dependencies, error=_error)

    @router.post(
        BUDGET_CONFIGURATION_PATH,
        operation_id="configure_ci_economics_budget",
        response_model=BudgetPolicyMutationResponse,
        responses={**_ERRORS, 409: {"model": BudgetPolicyMutationResponse}},
    )
    async def configure(
        request: Request,
        body: ConfigureBudgetPolicyBody,
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
        if isinstance(result, BudgetPolicyCommitted):
            response = BudgetPolicyMutationResponse(
                operation_id=body.operation_id,
                outcome="replayed" if result.replayed else "committed",
                policy=BudgetPolicyPayload.model_validate(result.policy.canonical_mapping()),
            )
            return JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        if isinstance(result, BudgetPolicyConflict):
            response = BudgetPolicyMutationResponse(
                operation_id=body.operation_id, outcome=result.reason, policy=None
            )
            return JSONResponse(response.to_wire_mapping(), status_code=409, headers=_NO_STORE)
        return (
            _error(403, "forbidden")
            if isinstance(result, CiEconomicsReadForbidden)
            else _error(503, "unavailable")
        )

    @router.get(
        BUDGET_POLICIES_PATH,
        operation_id="list_ci_economics_budget_policies",
        response_model=BudgetPoliciesResponse,
        responses=_ERRORS,
    )
    async def policies(
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
        result = await dependencies.use_case.list_policies(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
        )
        if isinstance(result, tuple):
            response = BudgetPoliciesResponse(
                installation_id=installation_id,
                repository_id=repository_id,
                policies=tuple(
                    BudgetPolicyPayload.model_validate(policy.canonical_mapping())
                    for policy in result
                ),
            )
            return JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        return (
            _error(403, "forbidden")
            if isinstance(result, CiEconomicsReadForbidden)
            else _error(503, "unavailable")
        )

    @router.get(
        BUDGET_SIGNALS_PATH,
        operation_id="list_ci_economics_budget_signals",
        response_model=BudgetSignalsResponse,
        responses=_ERRORS,
    )
    async def signals(
        request: Request,
        installation_id: ScopeId,
        repository_id: ScopeId,
        after_cursor: Annotated[BudgetDigest | None, Query(alias="afterCursor")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        policy_key: Annotated[BudgetKey | None, Query(alias="policyKey")] = None,
        revision: Annotated[int | None, Query(ge=1, le=MAX_SAFE_JSON_INTEGER)] = None,
        outcome: Annotated[ReportBudgetOutcome | None, Query()] = None,
        _security: Bearer = None,
        _session_security: Session = None,
    ) -> JSONResponse:
        admission = await admit(request, "audit")
        if isinstance(admission, JSONResponse):
            return admission
        keys = tuple(key for key, _ in request.query_params.multi_items())
        if (
            len(keys) != len(set(keys))
            or set(keys) - {"afterCursor", "limit", "policyKey", "revision", "outcome"}
            or (revision is not None and policy_key is None)
        ):
            return _error(400, "invalid_request")
        result = await dependencies.use_case.list_signals(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            after_cursor=after_cursor,
            limit=limit,
            policy_key=policy_key,
            revision=revision,
            outcome=outcome,
        )
        if isinstance(result, BudgetSignalPage):
            response = BudgetSignalsResponse(
                installation_id=installation_id,
                repository_id=repository_id,
                items=tuple(budget_signal_response(signal) for signal in result.items),
                next_cursor=result.next_cursor,
            )
            return JSONResponse(response.to_wire_mapping(), headers=_NO_STORE)
        return (
            _error(403, "forbidden")
            if isinstance(result, CiEconomicsReadForbidden)
            else _error(503, "unavailable")
        )

    return router


def _error(code: int, error: LiteralBudgetError) -> JSONResponse:
    return JSONResponse(
        EconomicsSourceErrorResponse(ok=False, error=error).to_wire_mapping(),
        status_code=code,
        headers={**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if code == 401 else {})},
    )
