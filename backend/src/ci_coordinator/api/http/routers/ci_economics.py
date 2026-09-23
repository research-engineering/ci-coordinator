"""Authenticated bounded CI economics projections."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.ci_economics_catalog_contracts import (
    EconomicsSourcePageResponse,
    SourceCursorQuery,
    catalog_query_is_admitted,
    source_page_response,
)
from ci_coordinator.api.http.ci_economics_contracts import (
    AttemptCursorQuery,
    CiEconomicsAttemptJobsResponse,
    CiEconomicsAttemptPageResponse,
    CiEconomicsErrorCode,
    CiEconomicsErrorResponse,
    attempt_jobs_projection,
    attempt_page_projection,
)
from ci_coordinator.api.http.ci_economics_measurement_contracts import (
    EconomicsMeasurementsResponse,
    measurements_response,
)
from ci_coordinator.api.http.contracts import InvalidRequestBody
from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiEconomicsRouteDependencies,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.ci_economics import (
    AttemptJobsAvailable,
    AttemptSummariesAvailable,
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics import (
    MAX_ECONOMICS_PAGE_SIZE,
    AttemptIdentity,
)
from ci_coordinator.ci_economics.catalog import ProviderSourcePage
from ci_coordinator.ci_economics.read_models import RecordedAttemptEconomics
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

CI_ECONOMICS_ATTEMPTS_PATH = (
    "/api/v1/economics/repositories/{installation_id}/{repository_id}/attempts"
)
CI_ECONOMICS_JOBS_PATH = CI_ECONOMICS_ATTEMPTS_PATH + "/{workflow_run_id}/{run_attempt}/jobs"
CI_ECONOMICS_MEASUREMENTS_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}"
    "/attempts/{workflow_run_id}/{run_attempt}/measurements"
)
CI_ECONOMICS_SOURCES_PATH = (
    "/api/v2/economics/repositories/{installation_id}/{repository_id}/sources"
)
_NO_STORE = {"Cache-Control": "no-store"}


def build_ci_economics_router(dependencies: CiEconomicsRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.get(
        CI_ECONOMICS_SOURCES_PATH,
        operation_id="list_ci_economics_provider_sources",
        response_model=EconomicsSourcePageResponse,
        responses=_responses(not_found=False),
    )
    async def list_provider_sources(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        after_cursor: Annotated[
            SourceCursorQuery | None, Query(alias="afterCursor", min_length=3, max_length=33)
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_ECONOMICS_PAGE_SIZE)] = 20,
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies)
        rejected = _admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("CI economics role admission was not granted")
        if not catalog_query_is_admitted(request.query_params.multi_items()):
            return JSONResponse({"code": "invalid_request"}, status_code=422, headers=_NO_STORE)
        result = await dependencies.use_case.list_provider_sources(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            after_cursor=after_cursor,
            limit=limit,
        )
        if isinstance(result, CiEconomicsReadForbidden):
            return _error(403, "forbidden")
        if isinstance(result, CiEconomicsReadUnavailable):
            return _error(503, "unavailable")
        if not isinstance(result, ProviderSourcePage):
            raise RuntimeError("CI economics catalog result is not admitted")
        return JSONResponse(source_page_response(result).to_wire_mapping(), headers=_NO_STORE)

    @router.get(
        CI_ECONOMICS_MEASUREMENTS_PATH,
        operation_id="get_ci_economics_attempt_measurements",
        response_model=EconomicsMeasurementsResponse,
        responses=_responses(not_found=True),
    )
    async def get_ci_economics_attempt_measurements(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        workflow_run_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        run_attempt: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        head_sha: Annotated[
            str,
            Query(alias="headSha", min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"),
        ],
        _security: Annotated[
            HTTPAuthorizationCredentials | None, Security(CONTROL_PLANE_BEARER)
        ] = None,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies)
        rejected = _admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("CI economics role admission was not granted")
        result = await dependencies.use_case.load_measurements(
            actor=admission.principal.actor_id,
            attempt=AttemptIdentity(
                RepositoryScope(installation_id, repository_id),
                workflow_run_id,
                run_attempt,
                head_sha,
            ),
        )
        if isinstance(result, CiEconomicsReadForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(result, CiEconomicsReadNotFound):
            return _error(status.HTTP_404_NOT_FOUND, "not_found")
        if isinstance(result, CiEconomicsReadUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(result, RecordedAttemptEconomics):
            raise RuntimeError("CI economics measurement outcome is incomplete")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=measurements_response(result).to_wire_mapping(),
            headers=_NO_STORE,
        )

    @router.get(
        CI_ECONOMICS_ATTEMPTS_PATH,
        operation_id="list_repository_ci_economics_attempts",
        response_model=CiEconomicsAttemptPageResponse,
        responses=_responses(not_found=False),
    )
    async def list_repository_ci_economics_attempts(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        after_cursor: Annotated[
            AttemptCursorQuery | None,
            Query(
                alias="afterCursor",
                min_length=92,
                max_length=92,
                pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\.[0-9a-f]{64}$",
            ),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_ECONOMICS_PAGE_SIZE)] = 50,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies)
        rejected = _admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("CI economics role admission was not granted")
        result = await dependencies.use_case.list_attempts(
            actor=admission.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            after_cursor=after_cursor,
            limit=limit,
        )
        if isinstance(result, CiEconomicsReadForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(result, CiEconomicsReadUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(result, AttemptSummariesAvailable):
            raise RuntimeError("CI economics attempt-page outcome is incomplete")
        response = CiEconomicsAttemptPageResponse.model_validate(
            attempt_page_projection(result.page)
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=_NO_STORE,
        )

    @router.get(
        CI_ECONOMICS_JOBS_PATH,
        operation_id="get_ci_economics_attempt_jobs",
        response_model=CiEconomicsAttemptJobsResponse,
        responses=_responses(not_found=True),
    )
    async def get_ci_economics_attempt_jobs(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        workflow_run_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        run_attempt: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        head_sha: Annotated[
            str,
            Query(alias="headSha", min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"),
        ],
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ] = None,
        after_job_id: Annotated[
            int | None,
            Query(alias="afterJobId", ge=1, le=MAX_SAFE_JSON_INTEGER),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_ECONOMICS_PAGE_SIZE)] = 50,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit(request, dependencies)
        rejected = _admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("CI economics role admission was not granted")
        result = await dependencies.use_case.load_attempt_jobs(
            actor=admission.principal.actor_id,
            attempt=AttemptIdentity(
                RepositoryScope(installation_id, repository_id),
                workflow_run_id,
                run_attempt,
                head_sha,
            ),
            after_job_id=after_job_id,
            limit=limit,
        )
        if isinstance(result, CiEconomicsReadForbidden):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(result, CiEconomicsReadNotFound):
            return _error(status.HTTP_404_NOT_FOUND, "not_found")
        if isinstance(result, CiEconomicsReadUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(result, AttemptJobsAvailable):
            raise RuntimeError("CI economics job-page outcome is incomplete")
        response = CiEconomicsAttemptJobsResponse.model_validate(attempt_jobs_projection(result))
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=_NO_STORE,
        )

    return router


async def _admit(
    request: Request,
    dependencies: CiEconomicsRouteDependencies,
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


def _admission_error(
    admission: RoleAdmissionGranted
    | InvalidCredential
    | ForbiddenIdentity
    | AuthenticationDependencyUnavailable,
) -> JSONResponse | None:
    if isinstance(admission, InvalidCredential):
        return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    if isinstance(admission, ForbiddenIdentity):
        return _error(status.HTTP_403_FORBIDDEN, "forbidden")
    if isinstance(admission, AuthenticationDependencyUnavailable):
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
    return None


def _responses(*, not_found: bool) -> dict[int | str, dict[str, object]]:
    responses: dict[int | str, dict[str, object]] = {
        status.HTTP_401_UNAUTHORIZED: {
            "model": CiEconomicsErrorResponse,
            "headers": WWW_AUTHENTICATE_OPENAPI,
        },
        status.HTTP_403_FORBIDDEN: {"model": CiEconomicsErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": InvalidRequestBody},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": CiEconomicsErrorResponse},
    }
    if not_found:
        responses[status.HTTP_404_NOT_FOUND] = {"model": CiEconomicsErrorResponse}
    return responses


def _error(status_code: int, error: CiEconomicsErrorCode) -> JSONResponse:
    response = CiEconomicsErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )
