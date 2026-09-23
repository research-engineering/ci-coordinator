"""Authenticated HTTP projection for validation-increasing operator overrides."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import Field, field_validator

from ci_coordinator.api.http.body_limits import BodyLimitPolicy
from ci_coordinator.api.http.contracts import ErrorBody
from ci_coordinator.api.http.control_plane_authentication import ControlPlaneRoleAuthorizer
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    InvalidCredential,
    OperatorControlsRouteDependencies,
)
from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.api.http.security import (
    CONTROL_PLANE_BEARER,
    CONTROL_PLANE_SESSION,
    WWW_AUTHENTICATE_HEADER,
    WWW_AUTHENTICATE_OPENAPI,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    BreakGlassPrincipal,
    ControlPlanePrincipal,
    ControlPlaneRole,
    IdentityRejected,
    RoleAdmissionGranted,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.operator_controls import (
    InvalidOverrideCommand,
    OverrideApplied,
    OverrideConflict,
    OverrideDuplicate,
    OverrideRejected,
    OverrideUnavailable,
    admit_override_command,
)

OPERATOR_OVERRIDE_PATH = "/api/v1/overrides/full-ci"
MAX_OPERATOR_OVERRIDE_BODY_BYTES = 16 * 1024
OPERATOR_OVERRIDE_BODY_LIMIT = BodyLimitPolicy(
    path=OPERATOR_OVERRIDE_PATH,
    maximum_body_bytes=MAX_OPERATOR_OVERRIDE_BODY_BYTES,
    invalid_content_length_response={"ok": False, "error": "invalid_override"},
    too_large_response={"ok": False, "error": "invalid_override"},
    overload_response={"ok": False, "error": "overloaded"},
    timeout_response={"ok": False, "error": "unavailable"},
)
type OverrideError = Literal[
    "unauthenticated", "forbidden", "invalid_override", "conflict", "overloaded", "unavailable"
]
_CANONICAL_ISO_DATETIME = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?\Z"
)


class _OverrideRequestBase(RequestModel):
    schema_version: Literal["operator-override/v1"] = Field(validation_alias="schemaVersion")
    installation_id: int = Field(
        validation_alias="installationId",
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    repository_id: int = Field(
        validation_alias="repositoryId",
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    operation_id: str = Field(
        validation_alias="operationId",
        min_length=1,
        max_length=512,
    )
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("operation_id", "reason")
    @classmethod
    def text_fits_the_domain_byte_contract(cls, value: str) -> str:
        return _bounded_utf8_text(value)


class ForceFullCIOverrideRequest(_OverrideRequestBase):
    kind: Literal["force_full_ci"]
    subject_id: str = Field(validation_alias="subjectId", min_length=1, max_length=512)
    expires_at: datetime = Field(validation_alias="expiresAt")

    @field_validator("expires_at", mode="before")
    @classmethod
    def parse_iso8601_expiry_text(cls, value: object) -> datetime:
        if type(value) is not str or _CANONICAL_ISO_DATETIME.fullmatch(value) is None:
            raise ValueError("expiresAt must be canonical ISO-8601 datetime text")
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("expiresAt must be canonical ISO-8601 datetime text") from error

    @field_validator("subject_id")
    @classmethod
    def subject_fits_the_domain_byte_contract(cls, value: str) -> str:
        return _bounded_utf8_text(value)


class DisableOmissionOverrideRequest(_OverrideRequestBase):
    kind: Literal["disable_omission"]


class EnableOmissionOverrideRequest(_OverrideRequestBase):
    kind: Literal["enable_omission"]
    override_id: str = Field(
        validation_alias="overrideId",
        pattern=r"^override_[0-9a-f]{32}$",
    )


type OverrideRequestBody = Annotated[
    ForceFullCIOverrideRequest | DisableOmissionOverrideRequest | EnableOmissionOverrideRequest,
    Field(discriminator="kind"),
]


def _bounded_utf8_text(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("override text must contain Unicode scalar values") from error
    if len(encoded) > 512:
        raise ValueError("override text exceeds its UTF-8 byte limit")
    return value


class OverrideAcceptedResponse(ResponseModel):
    ok: Literal[True]
    override_id: str
    duplicate: bool


class OverrideErrorResponse(ResponseModel):
    ok: Literal[False]
    error: OverrideError


def build_operator_controls_router(
    dependencies: OperatorControlsRouteDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        OPERATOR_OVERRIDE_PATH,
        operation_id="apply_validation_override",
        response_model=OverrideAcceptedResponse,
        responses={
            status.HTTP_400_BAD_REQUEST: {"model": OverrideErrorResponse},
            status.HTTP_401_UNAUTHORIZED: {
                "model": OverrideErrorResponse,
                "headers": WWW_AUTHENTICATE_OPENAPI,
            },
            status.HTTP_403_FORBIDDEN: {"model": OverrideErrorResponse},
            status.HTTP_409_CONFLICT: {"model": OverrideErrorResponse},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": OverrideErrorResponse},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorBody},
        },
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def apply_validation_override(
        body: OverrideRequestBody,
        request: Request,
        _security: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(CONTROL_PLANE_BEARER),
        ],
        _session_security: Annotated[str | None, Security(CONTROL_PLANE_SESSION)] = None,
    ) -> JSONResponse:
        admission = await _admit_override_request(request, body.kind, dependencies)
        if isinstance(admission, InvalidCredential):
            return _error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
        if isinstance(admission, ForbiddenIdentity):
            return _error(status.HTTP_403_FORBIDDEN, "forbidden")
        if isinstance(admission, AuthenticationDependencyUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        command = admit_override_command(
            kind=body.kind,
            scope=RepositoryScope(body.installation_id, body.repository_id),
            subject_id=(
                body.subject_id
                if isinstance(body, ForceFullCIOverrideRequest)
                else body.override_id
                if isinstance(body, EnableOmissionOverrideRequest)
                else None
            ),
            operation_id=body.operation_id,
            actor=admission.actor_id,
            reason=body.reason,
            expires_at=(body.expires_at if isinstance(body, ForceFullCIOverrideRequest) else None),
        )
        if isinstance(command, InvalidOverrideCommand):
            return _error(status.HTTP_400_BAD_REQUEST, command.reason)
        outcome = await dependencies.override_use_case(command)
        if isinstance(outcome, OverrideApplied):
            return _accepted(outcome.override.override_id, duplicate=False)
        if isinstance(outcome, OverrideDuplicate):
            return _accepted(outcome.override.override_id, duplicate=True)
        if isinstance(outcome, OverrideRejected):
            error: OverrideError = (
                "forbidden" if outcome.code == "unauthorized" else "invalid_override"
            )
            return _error(status.HTTP_403_FORBIDDEN if error == "forbidden" else 400, error)
        if isinstance(outcome, OverrideConflict):
            return _error(status.HTTP_409_CONFLICT, "conflict")
        if isinstance(outcome, OverrideUnavailable):
            return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
        raise RuntimeError("unsupported operator override outcome")

    return router


type _OverrideAdmission = (
    ControlPlanePrincipal
    | InvalidCredential
    | ForbiddenIdentity
    | AuthenticationDependencyUnavailable
)


async def _admit_override_request(
    request: Request,
    kind: Literal["force_full_ci", "disable_omission", "enable_omission"],
    dependencies: OperatorControlsRouteDependencies,
) -> _OverrideAdmission:
    authentication = await dependencies.authenticator.authenticate(request)
    if isinstance(authentication, InvalidCredential | AuthenticationDependencyUnavailable):
        return authentication
    if not dependencies.mutation_admission.admits(request, authentication):
        return ForbiddenIdentity()
    if isinstance(authentication, BreakGlassPrincipal):
        return (
            authentication if kind in {"force_full_ci", "disable_omission"} else ForbiddenIdentity()
        )
    required_roles: frozenset[ControlPlaneRole] = (
        frozenset({"activate", "override"})
        if kind == "enable_omission"
        else frozenset({"override"})
    )
    role_admission = dependencies.role_admission.admit(authentication, required_roles)
    if isinstance(role_admission, RoleAdmissionGranted):
        return role_admission.principal
    if not isinstance(role_admission, IdentityRejected):
        raise RuntimeError("unsupported control-plane override admission")
    if role_admission.code == "forbidden" and isinstance(
        dependencies.role_admission, ControlPlaneRoleAuthorizer
    ):
        await dependencies.role_admission.record_denial(authentication)
    return InvalidCredential() if role_admission.code == "unauthenticated" else ForbiddenIdentity()


def _accepted(override_id: str, *, duplicate: bool) -> JSONResponse:
    response = OverrideAcceptedResponse(
        ok=True,
        override_id=override_id,
        duplicate=duplicate,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=response.to_wire_mapping(),
        headers={"Cache-Control": "no-store"},
    )


def _error(status_code: int, error: OverrideError) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=OverrideErrorResponse(ok=False, error=error).to_wire_mapping(),
        headers={
            **(WWW_AUTHENTICATE_HEADER if status_code == 401 else {}),
            "Cache-Control": "no-store",
        },
    )
