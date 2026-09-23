"""Authenticated validation and bounded reads for config lifecycle."""

from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.config_lifecycle_contracts import (
    CONFIG_NO_STORE_HEADER,
    CONFIG_NO_STORE_OPENAPI,
    CONFIG_SOURCE_HEADERS_OPENAPI,
    ActiveConfigEpochBody,
    ConfigControlErrorBody,
    ConfigEpochStatusBody,
    ConfigEpochSummaryBody,
    ConfigEpochValidationAcceptedBody,
    ConfigEpochValidationBody,
    PolicyDiagnosticBody,
    admit_config_request,
    config_admission_error,
    config_error,
)
from ci_coordinator.api.http.contracts import ErrorBody
from ci_coordinator.api.http.dependencies import ConfigManagementRouteDependencies
from ci_coordinator.api.http.routers.config_management import CONFIG_VALIDATIONS_PATH
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app.config_admission import (
    ConfigAdmissionAccepted,
    ConfigAdmissionForbidden,
    ConfigAdmissionInvalid,
    ConfigAdmissionUnavailable,
)
from ci_coordinator.app.config_queries import (
    ConfigQueryForbidden,
    ConfigQueryNotFound,
    ConfigQueryUnavailable,
    ConfigSourceAvailable,
    ConfigStatusAvailable,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import MAX_CONFIG_EPOCH_PAGE_SIZE
from ci_coordinator.control_plane_identity import RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

CONFIG_STATUS_PATH = "/api/v1/config/repositories/{installation_id}/{repository_id}/status"
CONFIG_SOURCE_PATH = (
    "/api/v1/config/repositories/{installation_id}/{repository_id}/epochs/{epoch_id}/source"
)


def build_config_lifecycle_query_router(
    dependencies: ConfigManagementRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        CONFIG_VALIDATIONS_PATH,
        operation_id="validate_config_epoch",
        response_model=ConfigEpochValidationAcceptedBody,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ConfigControlErrorBody},
            status.HTTP_401_UNAUTHORIZED: {
                "model": ConfigControlErrorBody,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ConfigControlErrorBody},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": ConfigControlErrorBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody | ConfigControlErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConfigControlErrorBody},
        },
    )
    async def validate_config_epoch(
        body: ConfigEpochValidationBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        authentication = await admit_config_request(
            request,
            dependencies,
            frozenset({"configure"}),
            mutation=True,
        )
        rejected = config_admission_error(authentication)
        if rejected is not None:
            return rejected
        if not isinstance(authentication, RoleAdmissionGranted):
            raise RuntimeError("config validation admission was not granted")
        result = await dependencies.admission.admit(
            actor=authentication.principal.actor_id,
            source=body.source.encode("utf-8"),
            source_format=body.source_format,
        )
        if isinstance(result, ConfigAdmissionAccepted):
            draft = result.draft
            response = ConfigEpochValidationAcceptedBody(
                schema_version="ci-config-epoch-validation-result/v1",
                ok=True,
                installation_id=draft.scope.installation_id,
                repository_id=draft.scope.repository_id,
                epoch_id=draft.epoch_id,
                source_hash=draft.source_hash,
                document_hash=draft.document_hash,
                epoch_hash=draft.epoch_hash,
                document_schema_id=draft.document_schema_id,
                document_profile_id=draft.document_profile_id,
                semantic_profile_id=draft.semantic_profile_id,
                compiled_schema_id=draft.compiled_schema_id,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=response.to_wire_mapping(),
                headers=CONFIG_NO_STORE_HEADER,
            )
        if isinstance(result, ConfigAdmissionInvalid):
            diagnostics = tuple(
                PolicyDiagnosticBody(
                    code=item.code,
                    phase=item.phase,
                    rule_id=item.rule_id,
                    instance_pointer=item.instance_pointer,
                )
                for item in result.diagnostics
            )
            return config_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_config",
                diagnostics=diagnostics,
            )
        if isinstance(result, ConfigAdmissionForbidden):
            return config_error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(result, ConfigAdmissionUnavailable):
            return config_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        raise RuntimeError("config validation outcome algebra is incomplete")

    @router.get(
        CONFIG_STATUS_PATH,
        operation_id="get_config_epoch_status",
        response_model=ConfigEpochStatusBody,
        responses={
            status.HTTP_200_OK: {
                "model": ConfigEpochStatusBody,
                "headers": CONFIG_NO_STORE_OPENAPI,
            },
            status.HTTP_401_UNAUTHORIZED: {
                "model": ConfigControlErrorBody,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ConfigControlErrorBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConfigControlErrorBody},
        },
    )
    async def get_config_epoch_status(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        after_epoch_id: Annotated[
            str | None,
            Query(alias="afterEpochId", min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_CONFIG_EPOCH_PAGE_SIZE)] = 50,
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        authentication = await admit_config_request(
            request,
            dependencies,
            frozenset({"configure"}),
            mutation=False,
        )
        rejected = config_admission_error(authentication)
        if rejected is not None:
            return rejected
        if not isinstance(authentication, RoleAdmissionGranted):
            raise RuntimeError("config status admission was not granted")
        result = await dependencies.queries.status(
            actor=authentication.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            after_epoch_id=after_epoch_id,
            limit=limit,
        )
        if isinstance(result, ConfigQueryForbidden):
            return config_error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(result, ConfigQueryUnavailable):
            return config_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(result, ConfigStatusAvailable):
            raise RuntimeError("config status outcome algebra is incomplete")
        value = result.value
        response = ConfigEpochStatusBody(
            schema_version="ci-config-epoch-status/v1",
            installation_id=value.scope.installation_id,
            repository_id=value.scope.repository_id,
            active=(
                None
                if value.active is None
                else ActiveConfigEpochBody(
                    epoch_id=value.active.epoch_id,
                    revision=value.active.revision,
                )
            ),
            epochs=tuple(
                ConfigEpochSummaryBody(
                    epoch_id=item.epoch_id,
                    source_format=item.source_format,
                    source_hash=item.source_hash,
                    document_hash=item.document_hash,
                    epoch_hash=item.epoch_hash,
                    source_byte_count=item.source_byte_count,
                )
                for item in value.epochs.items
            ),
            next_cursor=value.epochs.next_cursor,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=CONFIG_NO_STORE_HEADER,
        )

    @router.get(
        CONFIG_SOURCE_PATH,
        operation_id="export_config_epoch_source",
        response_class=Response,
        responses={
            status.HTTP_200_OK: {
                "headers": CONFIG_SOURCE_HEADERS_OPENAPI,
                "content": {
                    "application/json": {},
                    "application/yaml": {},
                },
            },
            status.HTTP_401_UNAUTHORIZED: {
                "model": ConfigControlErrorBody,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ConfigControlErrorBody},
            status.HTTP_404_NOT_FOUND: {"model": ConfigControlErrorBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConfigControlErrorBody},
        },
    )
    async def export_config_epoch_source(
        request: Request,
        installation_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        repository_id: Annotated[int, Path(ge=1, le=MAX_SAFE_JSON_INTEGER)],
        epoch_id: Annotated[
            str,
            Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
        ],
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> Response:
        authentication = await admit_config_request(
            request,
            dependencies,
            frozenset({"configure"}),
            mutation=False,
        )
        rejected = config_admission_error(authentication)
        if rejected is not None:
            return rejected
        if not isinstance(authentication, RoleAdmissionGranted):
            raise RuntimeError("config source admission was not granted")
        result = await dependencies.queries.source(
            actor=authentication.principal.actor_id,
            scope=RepositoryScope(installation_id, repository_id),
            epoch_id=epoch_id,
        )
        if isinstance(result, ConfigQueryForbidden):
            return config_error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(result, ConfigQueryNotFound):
            return config_error(status.HTTP_404_NOT_FOUND, "target_unavailable")
        if isinstance(result, ConfigQueryUnavailable):
            return config_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        if not isinstance(result, ConfigSourceAvailable):
            raise RuntimeError("config source outcome algebra is incomplete")
        draft = result.value
        digest = sha256(draft.source_bytes).digest()
        return Response(
            content=draft.source_bytes,
            media_type="application/json" if draft.source_format == "json" else "application/yaml",
            headers={
                "Cache-Control": "no-store",
                "Content-Digest": f"sha-256=:{b64encode(digest).decode('ascii')}:",
                "ETag": f'"{draft.source_hash}"',
                "X-CI-Config-Epoch-Id": draft.epoch_id,
            },
        )

    return router
