"""Signed dynamic-plan HTTP route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.contracts import ErrorBody, PlanRequestBody, SignedPlanEnvelopeBody
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    PlanRouteDependencies,
)
from ci_coordinator.api.http.errors import TransportError
from ci_coordinator.api.http.security import (
    GITHUB_ACTIONS_OIDC_BEARER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.dynamic_plan import DynamicPlanCommand
from ci_coordinator.observability import (
    PlanOperationDiagnostic,
    bind_plan_operation_diagnostic,
)
from ci_coordinator.plan_issuance import (
    IssuanceConflict,
    IssuanceRejected,
    Issued,
    parse_plan_request,
)

PLAN_REQUEST_PATH = "/api/v1/dynamic-ci/plan"
MAX_PLAN_REQUEST_BODY_BYTES = 64 * 1024
PLAN_REQUEST_BODY_LIMIT = BodyLimitPolicy(
    path=PLAN_REQUEST_PATH,
    maximum_body_bytes=MAX_PLAN_REQUEST_BODY_BYTES,
    invalid_content_length_response={"code": "invalid_request"},
    too_large_response={"code": "request_too_large"},
    overload_response={"code": "plan_overloaded"},
    timeout_response={"code": "plan_unavailable"},
)


def build_plan_request_router(dependencies: PlanRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.post(
        PLAN_REQUEST_PATH,
        response_model=SignedPlanEnvelopeBody,
        responses={
            400: {"model": ErrorBody},
            401: {"model": ErrorBody, "headers": WWW_AUTHENTICATE_OPENAPI},
            403: {"model": ErrorBody},
            409: {"model": ErrorBody},
            413: {"model": ErrorBody},
            422: {"model": ErrorBody},
            503: {"model": ErrorBody},
        },
        status_code=status.HTTP_200_OK,
    )
    async def request_dynamic_plan(
        body: PlanRequestBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(GITHUB_ACTIONS_OIDC_BEARER),
        ],
    ) -> SignedPlanEnvelopeBody:
        parsed = parse_plan_request(body.to_wire_mapping())
        if isinstance(parsed, str):
            _observe_plan_result(dependencies, "invalid")
            return _raise_error(status.HTTP_400_BAD_REQUEST, "invalid_plan_request")
        authorization_values = tuple(
            value.decode("latin-1")
            for name, value in request.scope.get("headers", ())
            if name.lower() == b"authorization"
        )
        if len(authorization_values) != 1:
            _observe_plan_result(dependencies, "unauthenticated")
            return _raise_error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        identity = await dependencies.authenticator.authenticate(authorization_values[0], parsed)
        if isinstance(identity, InvalidCredential):
            _observe_plan_result(dependencies, "unauthenticated")
            return _raise_error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        if isinstance(identity, ForbiddenIdentity):
            _observe_plan_result(dependencies, "forbidden")
            return _raise_error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(identity, AuthenticationDependencyUnavailable):
            _observe_plan_result(dependencies, "dependency_unavailable")
            return _raise_error(status.HTTP_503_SERVICE_UNAVAILABLE, "plan_unavailable")
        outcome = await dependencies.use_case.request_dynamic_plan(
            DynamicPlanCommand(parsed, identity)
        )
        if isinstance(outcome, Issued):
            _observe_issued(dependencies, request, outcome)
            return SignedPlanEnvelopeBody.model_validate(_serialize_envelope(outcome))
        if isinstance(outcome, IssuanceConflict):
            _observe_plan_result(dependencies, "conflict")
            return _raise_error(status.HTTP_409_CONFLICT, "plan_request_conflict")
        if isinstance(outcome, IssuanceRejected):
            _observe_plan_result(dependencies, "issuance_unavailable")
            return _raise_error(status.HTTP_503_SERVICE_UNAVAILABLE, "plan_unavailable")
        raise RuntimeError("unsupported dynamic plan outcome")

    return router


def _serialize_envelope(outcome: Issued) -> dict[str, object]:
    envelope = outcome.record.envelope
    return {
        "schema_version": envelope.schema_version,
        "key_id": envelope.key_id,
        "algorithm": envelope.algorithm,
        "issued_at": envelope.issued_at.isoformat(),
        "expires_at": envelope.expires_at.isoformat(),
        "payload": envelope.payload.identity_mapping(),
        "signature": envelope.signature,
    }


def _observe_plan_result(dependencies: PlanRouteDependencies, result: str) -> None:
    if dependencies.runtime_metrics is not None:
        dependencies.runtime_metrics.plan_request(result)


def _observe_issued(
    dependencies: PlanRouteDependencies,
    request: Request,
    outcome: Issued,
) -> None:
    metrics = dependencies.runtime_metrics
    if metrics is None:
        pass
    else:
        metrics.plan_request("issued")
        metrics.signed_envelope(duplicate=outcome.duplicate)
        reason = outcome.record.envelope.payload.fallback_reason
        if reason is not None:
            metrics.full_ci_fallback(reason)
    payload = outcome.record.envelope.payload
    bind_plan_operation_diagnostic(
        request.scope,
        PlanOperationDiagnostic(
            issued_plan_record_id=outcome.record.record_id,
            plan_id=payload.plan_id,
            repository_id=payload.repository.repository_id,
            workflow_run_id=payload.request.workflow_run_id,
            run_attempt=payload.request.run_attempt,
        ),
    )


def _raise_error(status_code: int, code: str) -> SignedPlanEnvelopeBody:
    raise TransportError(status_code, code)
