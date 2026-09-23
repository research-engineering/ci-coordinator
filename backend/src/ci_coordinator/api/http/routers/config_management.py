"""Authenticated HTTP control plane for repository policy hot reload."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.config_lifecycle_contracts import (
    CONFIG_NO_STORE_HEADER,
    ConfigActivationAcceptedBody,
    ConfigControlErrorBody,
    ConfigEpochAcceptedBody,
    ConfigEpochActivationBody,
    ConfigEpochRegistrationBody,
    ConfigEpochRollbackBody,
    PolicyDiagnosticBody,
    admit_config_request,
    config_admission_error,
    config_error,
)
from ci_coordinator.api.http.contracts import ErrorBody
from ci_coordinator.api.http.dependencies import ConfigManagementRouteDependencies
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.app import (
    ActivateConfigEpoch,
    ConfigActivationOutcome,
    RegisterConfigEpoch,
    RollbackConfigEpoch,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_control.limits import MAX_POLICY_SOURCE_BYTES
from ci_coordinator.config_epochs import MAX_CONFIG_OPERATION_ID_UTF8_BYTES
from ci_coordinator.control_plane_identity import RoleAdmissionGranted

CONFIG_EPOCHS_PATH = "/api/v1/config/epochs"
CONFIG_VALIDATIONS_PATH = "/api/v1/config/validations"
CONFIG_ACTIVATIONS_PATH = "/api/v1/config/activations"
CONFIG_ROLLBACKS_PATH = "/api/v1/config/rollbacks"
MAX_CONFIG_REGISTRATION_BODY_BYTES = (
    MAX_POLICY_SOURCE_BYTES * 6 + MAX_CONFIG_OPERATION_ID_UTF8_BYTES * 6 + 1_024
)
MAX_CONFIG_COMMAND_BODY_BYTES = 16 * 1024


def config_control_error_payload(
    error: Literal["invalid_config", "overloaded", "unavailable"],
) -> dict[str, object]:
    return {"ok": False, "error": error, "diagnostics": []}


CONFIG_MANAGEMENT_BODY_LIMITS = (
    BodyLimitPolicy(
        path=CONFIG_VALIDATIONS_PATH,
        maximum_body_bytes=MAX_CONFIG_REGISTRATION_BODY_BYTES,
        invalid_content_length_response=config_control_error_payload("invalid_config"),
        too_large_response=config_control_error_payload("invalid_config"),
        overload_response=config_control_error_payload("overloaded"),
        timeout_response=config_control_error_payload("unavailable"),
    ),
    BodyLimitPolicy(
        path=CONFIG_EPOCHS_PATH,
        maximum_body_bytes=MAX_CONFIG_REGISTRATION_BODY_BYTES,
        invalid_content_length_response=config_control_error_payload("invalid_config"),
        too_large_response=config_control_error_payload("invalid_config"),
        overload_response=config_control_error_payload("overloaded"),
        timeout_response=config_control_error_payload("unavailable"),
    ),
    BodyLimitPolicy(
        path=CONFIG_ACTIVATIONS_PATH,
        maximum_body_bytes=MAX_CONFIG_COMMAND_BODY_BYTES,
        invalid_content_length_response=config_control_error_payload("invalid_config"),
        too_large_response=config_control_error_payload("invalid_config"),
        overload_response=config_control_error_payload("overloaded"),
        timeout_response=config_control_error_payload("unavailable"),
    ),
    BodyLimitPolicy(
        path=CONFIG_ROLLBACKS_PATH,
        maximum_body_bytes=MAX_CONFIG_COMMAND_BODY_BYTES,
        invalid_content_length_response=config_control_error_payload("invalid_config"),
        too_large_response=config_control_error_payload("invalid_config"),
        overload_response=config_control_error_payload("overloaded"),
        timeout_response=config_control_error_payload("unavailable"),
    ),
)


def build_config_management_router(
    dependencies: ConfigManagementRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        CONFIG_EPOCHS_PATH,
        operation_id="register_config_epoch",
        response_model=ConfigEpochAcceptedBody,
        status_code=status.HTTP_201_CREATED,
        responses={
            status.HTTP_200_OK: {"model": ConfigEpochAcceptedBody},
            status.HTTP_400_BAD_REQUEST: {"model": ConfigControlErrorBody},
            status.HTTP_401_UNAUTHORIZED: {
                "model": ConfigControlErrorBody,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ConfigControlErrorBody},
            status.HTTP_409_CONFLICT: {"model": ConfigControlErrorBody},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": ConfigControlErrorBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody | ConfigControlErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConfigControlErrorBody},
        },
    )
    async def register_config_epoch(
        body: ConfigEpochRegistrationBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit_config_request(
            request,
            dependencies,
            frozenset({"configure"}),
            mutation=True,
        )
        rejected = config_admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("control-plane mutation admission was not granted")
        result = await dependencies.use_case.register(
            RegisterConfigEpoch(
                admission.principal.actor_id,
                body.source.encode("utf-8"),
                body.source_format,
                body.operation_id,
            )
        )
        if result.state in {"created", "duplicate"}:
            epoch_id = cast(str, result.epoch_id)
            response = ConfigEpochAcceptedBody(
                schema_version="ci-config-epoch-registration-result/v1",
                ok=True,
                epoch_id=epoch_id,
                duplicate=result.state == "duplicate",
            )
            return JSONResponse(
                status_code=(
                    status.HTTP_200_OK if result.state == "duplicate" else status.HTTP_201_CREATED
                ),
                content=response.to_wire_mapping(),
                headers=CONFIG_NO_STORE_HEADER,
            )
        if result.state == "invalid":
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
        if result.state == "forbidden":
            return config_error(status.HTTP_403_FORBIDDEN, "forbidden")
        if result.state == "conflict":
            return config_error(status.HTTP_409_CONFLICT, "conflict")
        if result.state == "unavailable":
            return config_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        raise RuntimeError("unsupported config registration outcome")

    @router.post(
        CONFIG_ACTIVATIONS_PATH,
        operation_id="activate_config_epoch",
        response_model=ConfigActivationAcceptedBody,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ConfigControlErrorBody},
            status.HTTP_401_UNAUTHORIZED: {
                "model": ConfigControlErrorBody,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ConfigControlErrorBody},
            status.HTTP_404_NOT_FOUND: {"model": ConfigControlErrorBody},
            status.HTTP_409_CONFLICT: {"model": ConfigControlErrorBody},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": ConfigControlErrorBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConfigControlErrorBody},
        },
    )
    async def activate_config_epoch(
        body: ConfigEpochActivationBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit_config_request(
            request, dependencies, frozenset({"activate"}), mutation=True
        )
        rejected = config_admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("control-plane mutation admission was not granted")
        result = await dependencies.use_case.activate(
            ActivateConfigEpoch(
                actor=admission.principal.actor_id,
                scope=RepositoryScope(body.installation_id, body.repository_id),
                target_epoch_id=body.target_epoch_id,
                proposal_manifest_id=body.proposal_manifest_id,
                expected_revision=body.expected_revision,
                operation_id=body.operation_id,
            )
        )
        return _activation_response(result)

    @router.post(
        CONFIG_ROLLBACKS_PATH,
        operation_id="rollback_config_epoch",
        response_model=ConfigActivationAcceptedBody,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": ConfigControlErrorBody},
            status.HTTP_401_UNAUTHORIZED: {
                "model": ConfigControlErrorBody,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": ConfigControlErrorBody},
            status.HTTP_404_NOT_FOUND: {"model": ConfigControlErrorBody},
            status.HTTP_409_CONFLICT: {"model": ConfigControlErrorBody},
            status.HTTP_413_CONTENT_TOO_LARGE: {"model": ConfigControlErrorBody},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ConfigControlErrorBody},
        },
    )
    async def rollback_config_epoch(
        body: ConfigEpochRollbackBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await admit_config_request(
            request, dependencies, frozenset({"activate"}), mutation=True
        )
        rejected = config_admission_error(admission)
        if rejected is not None:
            return rejected
        if not isinstance(admission, RoleAdmissionGranted):
            raise RuntimeError("control-plane mutation admission was not granted")
        result = await dependencies.use_case.rollback(
            RollbackConfigEpoch(
                actor=admission.principal.actor_id,
                scope=RepositoryScope(body.installation_id, body.repository_id),
                target_epoch_id=body.target_epoch_id,
                expected_revision=body.expected_revision,
                operation_id=body.operation_id,
                reason=body.reason,
            )
        )
        return _activation_response(result)

    return router


def _activation_response(result: ConfigActivationOutcome) -> JSONResponse:
    if result.state in {"applied", "duplicate"}:
        epoch_id = cast(str, result.epoch_id)
        revision = cast(int, result.revision)
        response = ConfigActivationAcceptedBody(
            schema_version="ci-config-epoch-activation-result/v1",
            ok=True,
            epoch_id=epoch_id,
            revision=revision,
            duplicate=result.state == "duplicate",
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.to_wire_mapping(),
            headers=CONFIG_NO_STORE_HEADER,
        )
    if result.state == "forbidden":
        return config_error(status.HTTP_403_FORBIDDEN, "forbidden")
    if result.state == "revision_conflict":
        return config_error(status.HTTP_409_CONFLICT, "revision_conflict")
    if result.state == "target_unavailable":
        return config_error(status.HTTP_404_NOT_FOUND, "target_unavailable")
    if result.state == "operation_conflict":
        return config_error(status.HTTP_409_CONFLICT, "conflict")
    if result.state == "attestation_invalid":
        return config_error(status.HTTP_409_CONFLICT, "attestation_invalid")
    if result.state == "coverage_reducing":
        return config_error(status.HTTP_409_CONFLICT, "coverage_reducing")
    if result.state == "coverage_unproven":
        return config_error(status.HTTP_409_CONFLICT, "coverage_unproven")
    if result.state == "unavailable":
        return config_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
    raise RuntimeError("unsupported config activation outcome")
