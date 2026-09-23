from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.ci_economics_catalog_contracts import (
    MeasurementReportPageResponse,
    catalog_query_is_admitted,
    report_page_response,
)
from ci_coordinator.api.http.ci_economics_contracts import CiEconomicsErrorResponse
from ci_coordinator.api.http.ci_measurement_report_contracts import (
    MeasurementReportBudgetResponse,
    MeasurementReportComparisonResponse,
    RetainedMeasurementReportResponse,
    report_budget_response,
    report_comparison_response,
    retained_report_response,
)
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    MeasurementReportReadRouteDependencies,
)
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.ci_economics import (
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.budget import EvaluatedReportBudget, ReportBudget
from ci_coordinator.ci_economics.catalog import MeasurementReportPage
from ci_coordinator.ci_economics.comparison import RetainedReportPair
from ci_coordinator.ci_economics.model import MAX_ECONOMICS_PAGE_SIZE
from ci_coordinator.ci_economics.reports import ReportCounter, StoredMeasurementReport
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

REPORT_READ_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/reports/{report_id}"
)
REPORT_COMPARISON_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/report-comparisons"
)
REPORT_BUDGET_PATH = REPORT_READ_PATH + "/budget"
REPORT_CATALOG_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/sources/{source_id}/reports"
)
_NO_STORE = {"Cache-Control": "no-store"}
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": CiEconomicsErrorResponse, "headers": WWW_AUTHENTICATE_OPENAPI},
    403: {"model": CiEconomicsErrorResponse},
    404: {"model": CiEconomicsErrorResponse},
    422: {"model": InvalidRequestBody},
    503: {"model": CiEconomicsErrorResponse},
}


def build_measurement_report_read_router(
    dependencies: MeasurementReportReadRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    async def admit(
        request: Request,
    ) -> (
        RoleAdmissionGranted
        | InvalidCredential
        | ForbiddenIdentity
        | AuthenticationDependencyUnavailable
    ):
        return await authenticate_and_admit_roles(
            request,
            authenticator=dependencies.authenticator,
            role_admission=dependencies.role_admission,
            required_roles=frozenset({"audit"}),
        )

    @router.get(
        REPORT_CATALOG_PATH,
        operation_id="list_ci_measurement_reports",
        response_model=MeasurementReportPageResponse,
        responses=_ERROR_RESPONSES,
    )
    async def list_reports(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        source_id: Annotated[str, Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")],
        after_cursor: Annotated[
            str | None,
            Query(alias="afterCursor", min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_ECONOMICS_PAGE_SIZE)] = 20,
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit(request)
        if not isinstance(admission, RoleAdmissionGranted):
            return _error(admission)
        if not catalog_query_is_admitted(request.query_params.multi_items()):
            return JSONResponse({"code": "invalid_request"}, status_code=422, headers=_NO_STORE)
        result = await dependencies.use_case.list_reports(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            source_id=source_id,
            after_cursor=after_cursor,
            limit=limit,
        )
        if not isinstance(result, MeasurementReportPage):
            return _error(result)
        return JSONResponse(report_page_response(result).to_wire_mapping(), headers=_NO_STORE)

    @router.get(
        REPORT_READ_PATH,
        operation_id="get_ci_measurement_report",
        response_model=RetainedMeasurementReportResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get_report(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        report_id: Annotated[str, Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")],
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit(request)
        if not isinstance(admission, RoleAdmissionGranted):
            return _error(admission)
        result = await dependencies.use_case.load_report(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            report_id=report_id,
        )
        if not isinstance(result, StoredMeasurementReport):
            return _error(result)
        return JSONResponse(
            content=retained_report_response(result).to_wire_mapping(), headers=_NO_STORE
        )

    @router.get(
        REPORT_COMPARISON_PATH,
        operation_id="compare_ci_measurement_reports",
        response_model=MeasurementReportComparisonResponse,
        responses=_ERROR_RESPONSES,
    )
    async def compare_reports(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        baseline_id: Annotated[
            str,
            Query(
                alias="baselineReportId", min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
            ),
        ],
        treatment_id: Annotated[
            str,
            Query(
                alias="treatmentReportId", min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
            ),
        ],
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit(request)
        if not isinstance(admission, RoleAdmissionGranted):
            return _error(admission)
        result = await dependencies.use_case.compare_reports(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            baseline_id=baseline_id,
            treatment_id=treatment_id,
        )
        if not isinstance(result, RetainedReportPair):
            return _error(result)
        return JSONResponse(
            content=report_comparison_response(result).to_wire_mapping(), headers=_NO_STORE
        )

    @router.get(
        REPORT_BUDGET_PATH,
        operation_id="evaluate_ci_measurement_report_budget",
        response_model=MeasurementReportBudgetResponse,
        responses=_ERROR_RESPONSES,
    )
    async def evaluate_budget(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        report_id: Annotated[str, Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")],
        counter: Annotated[ReportCounter, Query()],
        maximum_us: Annotated[int, Query(alias="maximumUs", ge=0, le=MAX_SAFE_JSON_INTEGER)],
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit(request)
        if not isinstance(admission, RoleAdmissionGranted):
            return _error(admission)
        result = await dependencies.use_case.evaluate_budget(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            report_id=report_id,
            budget=ReportBudget(counter, maximum_us),
        )
        if not isinstance(result, EvaluatedReportBudget):
            return _error(result)
        return JSONResponse(report_budget_response(result).to_wire_mapping(), headers=_NO_STORE)

    return router


def _error(result: object) -> JSONResponse:
    if isinstance(result, InvalidCredential):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "unauthenticated"},
            headers={**_NO_STORE, **WWW_AUTHENTICATE_HEADER},
        )
    if isinstance(result, (ForbiddenIdentity, CiEconomicsReadForbidden)):
        return JSONResponse(
            status_code=403, content={"ok": False, "error": "forbidden"}, headers=_NO_STORE
        )
    if isinstance(result, CiEconomicsReadNotFound):
        return JSONResponse(
            status_code=404, content={"ok": False, "error": "not_found"}, headers=_NO_STORE
        )
    if isinstance(result, (AuthenticationDependencyUnavailable, CiEconomicsReadUnavailable)):
        return JSONResponse(
            status_code=503, content={"ok": False, "error": "unavailable"}, headers=_NO_STORE
        )
    raise RuntimeError("unsupported measurement report read outcome")
