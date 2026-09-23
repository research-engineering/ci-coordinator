from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.ci_measurement_report_submission import (
    MeasurementReportError,
    MeasurementReportErrorCode,
    MeasurementReportReceipt,
    MeasurementReportSubmission,
)
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    MeasurementReportIngestionRouteDependencies,
)
from ci_coordinator.api.http.request_admission import RequestAdmissionPolicy
from ci_coordinator.api.http.security import (
    GITHUB_ACTIONS_OIDC_BEARER,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.api.http.strict_json_route import strict_json_route
from ci_coordinator.ci_economics.reports import MAX_REPORT_BYTES
from ci_coordinator.kernel.canonical_json import JsonResourceLimits

REPORT_INGESTION_PATH: Final = "/api/v2/economics/reports"
_NO_STORE = {"Cache-Control": "no-store"}
REPORT_BODY_LIMIT = BodyLimitPolicy(
    path=REPORT_INGESTION_PATH,
    maximum_body_bytes=MAX_REPORT_BYTES,
    invalid_content_length_response={"ok": False, "error": "invalid_request"},
    too_large_response={"ok": False, "error": "invalid_request"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
REPORT_REQUEST_LIMIT = RequestAdmissionPolicy(
    path=REPORT_INGESTION_PATH,
    methods=frozenset({"POST"}),
    concurrency_limit=2,
    admission_key="ci_measurement_report_ingestion",
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
    response_headers=((b"cache-control", b"no-store"),),
)


_ReportJsonRoute = strict_json_route(
    methods=None,
    maximum_bytes=MAX_REPORT_BYTES,
    resource_limits=JsonResourceLimits(max_depth=5, max_nodes=150),
    invalid_response=lambda: _error(400, "invalid_request"),
)


def build_measurement_report_ingestion_router(
    dependencies: MeasurementReportIngestionRouteDependencies,
) -> APIRouter:
    router = APIRouter(route_class=_ReportJsonRoute)

    @router.post(
        REPORT_INGESTION_PATH,
        operation_id="record_ci_measurement_report",
        status_code=201,
        response_model=MeasurementReportReceipt,
        responses={
            200: {"model": MeasurementReportReceipt},
            400: {"model": MeasurementReportError},
            401: {"model": MeasurementReportError, "headers": WWW_AUTHENTICATE_OPENAPI},
            403: {"model": MeasurementReportError},
            409: {"model": MeasurementReportError},
            410: {"model": MeasurementReportError},
            413: {"model": MeasurementReportError},
            422: {"model": InvalidRequestBody},
            429: {"model": MeasurementReportError},
            503: {"model": MeasurementReportError},
        },
    )
    async def record_report(
        body: MeasurementReportSubmission,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(GITHUB_ACTIONS_OIDC_BEARER)
        ] = None,
    ) -> JSONResponse:
        authorizations = tuple(
            value.decode("latin-1")
            for name, value in request.scope["headers"]
            if name.lower() == b"authorization"
        )
        if len(authorizations) != 1:
            return _error(401, "unauthenticated")
        report = body.report.to_report()
        identity = await dependencies.authenticator.authenticate_run(
            authorizations[0],
            repository=body.repository,
            repository_id=report.attempt.scope.repository_id,
            ref=body.ref,
            run_id=report.attempt.workflow_run_id,
            run_attempt=report.attempt.run_attempt,
            event_name=body.event_name,
            execution_sha=body.execution_sha,
        )
        if isinstance(identity, InvalidCredential):
            return _error(401, "unauthenticated")
        if isinstance(identity, ForbiddenIdentity):
            return _error(403, "forbidden")
        if isinstance(identity, AuthenticationDependencyUnavailable):
            return _error(503, "unavailable")
        outcome = await dependencies.use_case.record_report(
            report, identity, page_number=body.job_page
        )
        if outcome in {"recorded", "replayed"}:
            receipt = MeasurementReportReceipt(
                status="recorded" if outcome == "recorded" else "replayed",
                report_id=report.report_id,
                report_digest=report.report_digest,
            )
            return JSONResponse(
                receipt.to_wire_mapping(),
                status_code=201 if outcome == "recorded" else 200,
                headers=_NO_STORE,
            )
        match outcome:
            case "forbidden":
                return _error(403, outcome)
            case "report_conflict" | "source_unavailable":
                return _error(409, outcome)
            case "outside_retention":
                return _error(410, outcome)
            case "capacity_reached":
                return _error(429, outcome)
            case "unavailable":
                return _error(503, outcome)
            case _:
                raise RuntimeError("unsupported measurement report ingestion result")

    return router


def _error(status: int, error: MeasurementReportErrorCode) -> JSONResponse:
    headers = {**_NO_STORE, **(WWW_AUTHENTICATE_HEADER if status == 401 else {})}
    return JSONResponse(
        MeasurementReportError(error=error).to_wire_mapping(), status_code=status, headers=headers
    )
