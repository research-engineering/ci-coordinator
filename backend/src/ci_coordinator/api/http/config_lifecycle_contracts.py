"""Versioned HTTP models and shared admission mapping for config lifecycle."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from ci_coordinator.api.http.control_plane_authentication import authenticate_and_admit_roles
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    ConfigManagementRouteDependencies,
    ForbiddenIdentity,
    InvalidCredential,
)
from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.api.http.security import WWW_AUTHENTICATE_HEADER
from ci_coordinator.config_control import PolicyPhase
from ci_coordinator.config_control.limits import (
    MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
    MAX_POLICY_SOURCE_BYTES,
)
from ci_coordinator.config_epochs import (
    MAX_CONFIG_EPOCH_PAGE_SIZE,
    MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
    MAX_ROLLBACK_REASON_UTF8_BYTES,
    validate_rollback_reason,
)
from ci_coordinator.control_plane_identity import ControlPlaneRole, RoleAdmissionGranted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
CONFIG_NO_STORE_HEADER = {"Cache-Control": "no-store"}
CONFIG_NO_STORE_OPENAPI = {
    "Cache-Control": {
        "description": "The configuration projection is never cacheable.",
        "schema": {"type": "string", "const": "no-store"},
    }
}
CONFIG_SOURCE_HEADERS_OPENAPI = {
    **CONFIG_NO_STORE_OPENAPI,
    "Content-Digest": {
        "description": "RFC 9530 SHA-256 digest of the exact retained source bytes.",
        "schema": {"type": "string"},
    },
    "ETag": {
        "description": "Strong validator equal to the retained source SHA-256.",
        "schema": {"type": "string"},
    },
    "X-CI-Config-Epoch-Id": {
        "description": "Content-addressed configuration epoch identity.",
        "schema": {"type": "string", "pattern": _SHA256_PATTERN},
    },
}

type ConfigContractResourceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_CONFIG_CONTRACT_ID_UTF8_BYTES},
    ),
]
type JsonSafePositiveInteger = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]
type LowercaseSha256Hex = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=_SHA256_PATTERN),
]
type PolicySourceByteCount = Annotated[int, Field(ge=1, le=MAX_POLICY_SOURCE_BYTES)]


class ConfigEpochValidationBody(RequestModel):
    schema_version: Literal["ci-config-epoch-validation/v1"] = Field(
        validation_alias="schemaVersion"
    )
    source_format: Literal["json", "yaml-1.2"] = Field(validation_alias="sourceFormat")
    source: str = Field(
        min_length=1,
        max_length=MAX_POLICY_SOURCE_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_POLICY_SOURCE_BYTES},
    )

    @field_validator("source")
    @classmethod
    def source_fits_the_domain_byte_contract(cls, value: str) -> str:
        return _validate_source(value)


class ConfigEpochRegistrationBody(RequestModel):
    schema_version: Literal["ci-config-epoch-registration/v1"] = Field(
        validation_alias="schemaVersion"
    )
    source_format: Literal["json", "yaml-1.2"] = Field(validation_alias="sourceFormat")
    source: str = Field(
        min_length=1,
        max_length=MAX_POLICY_SOURCE_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_POLICY_SOURCE_BYTES},
    )
    operation_id: str = Field(
        validation_alias="operationId",
        min_length=1,
        max_length=MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_CONFIG_OPERATION_ID_UTF8_BYTES},
    )

    @field_validator("source")
    @classmethod
    def source_fits_the_domain_byte_contract(cls, value: str) -> str:
        return _validate_source(value)

    @field_validator("operation_id")
    @classmethod
    def operation_id_fits_the_persistence_contract(cls, value: str) -> str:
        return _validate_operation_id(value)


class ConfigEpochActivationBody(RequestModel):
    schema_version: Literal["ci-config-epoch-activation/v1"] = Field(
        validation_alias="schemaVersion"
    )
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
    target_epoch_id: str = Field(
        validation_alias="targetEpochId",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    proposal_manifest_id: str = Field(
        validation_alias="proposalManifestId",
        min_length=41,
        max_length=41,
        pattern=r"^proposal:[0-9a-f]{32}$",
    )
    expected_revision: int | None = Field(
        default=None,
        validation_alias="expectedRevision",
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    operation_id: str = Field(
        validation_alias="operationId",
        min_length=1,
        max_length=MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_CONFIG_OPERATION_ID_UTF8_BYTES},
    )

    @field_validator("operation_id")
    @classmethod
    def operation_id_fits_the_persistence_contract(cls, value: str) -> str:
        return _validate_operation_id(value)


class ConfigEpochRollbackBody(RequestModel):
    schema_version: Literal["ci-config-epoch-rollback/v1"] = Field(validation_alias="schemaVersion")
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
    target_epoch_id: str = Field(
        validation_alias="targetEpochId",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_revision: int = Field(
        validation_alias="expectedRevision",
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    operation_id: str = Field(
        validation_alias="operationId",
        min_length=1,
        max_length=MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_CONFIG_OPERATION_ID_UTF8_BYTES},
    )
    reason: str = Field(
        min_length=1,
        max_length=MAX_ROLLBACK_REASON_UTF8_BYTES,
        json_schema_extra={"x-max-utf8-bytes": MAX_ROLLBACK_REASON_UTF8_BYTES},
    )

    @field_validator("operation_id")
    @classmethod
    def operation_id_fits_the_persistence_contract(cls, value: str) -> str:
        return _validate_operation_id(value)

    @field_validator("reason")
    @classmethod
    def reason_fits_the_governed_audit_contract(cls, value: str) -> str:
        return validate_rollback_reason(value)


class PolicyDiagnosticBody(ResponseModel):
    code: str
    phase: PolicyPhase
    rule_id: str
    instance_pointer: str


class ConfigEpochValidationAcceptedBody(ResponseModel):
    schema_version: Literal["ci-config-epoch-validation-result/v1"]
    ok: Literal[True]
    installation_id: JsonSafePositiveInteger
    repository_id: JsonSafePositiveInteger
    epoch_id: LowercaseSha256Hex
    source_hash: LowercaseSha256Hex
    document_hash: LowercaseSha256Hex
    epoch_hash: LowercaseSha256Hex
    document_schema_id: ConfigContractResourceId
    document_profile_id: ConfigContractResourceId
    semantic_profile_id: ConfigContractResourceId
    compiled_schema_id: ConfigContractResourceId

    @field_validator(
        "document_schema_id",
        "document_profile_id",
        "semantic_profile_id",
        "compiled_schema_id",
    )
    @classmethod
    def contract_resource_id_fits_the_domain_byte_contract(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_CONFIG_CONTRACT_ID_UTF8_BYTES:
            raise ValueError("contract resource identity exceeds its UTF-8 byte limit")
        return value


class ConfigEpochAcceptedBody(ResponseModel):
    schema_version: Literal["ci-config-epoch-registration-result/v1"]
    ok: Literal[True]
    epoch_id: LowercaseSha256Hex
    duplicate: bool


class ConfigActivationAcceptedBody(ResponseModel):
    schema_version: Literal["ci-config-epoch-activation-result/v1"]
    ok: Literal[True]
    epoch_id: LowercaseSha256Hex
    duplicate: bool
    revision: JsonSafePositiveInteger


class ActiveConfigEpochBody(ResponseModel):
    epoch_id: LowercaseSha256Hex
    revision: JsonSafePositiveInteger


class ConfigEpochSummaryBody(ResponseModel):
    epoch_id: LowercaseSha256Hex
    source_format: Literal["json", "yaml-1.2"]
    source_hash: LowercaseSha256Hex
    document_hash: LowercaseSha256Hex
    epoch_hash: LowercaseSha256Hex
    source_byte_count: PolicySourceByteCount


class ConfigEpochStatusBody(ResponseModel):
    schema_version: Literal["ci-config-epoch-status/v1"]
    installation_id: JsonSafePositiveInteger
    repository_id: JsonSafePositiveInteger
    active: ActiveConfigEpochBody | None
    epochs: tuple[ConfigEpochSummaryBody, ...] = Field(max_length=MAX_CONFIG_EPOCH_PAGE_SIZE)
    next_cursor: LowercaseSha256Hex | None


class ConfigControlErrorBody(ResponseModel):
    ok: Literal[False]
    error: Literal[
        "unauthenticated",
        "forbidden",
        "invalid_config",
        "conflict",
        "revision_conflict",
        "target_unavailable",
        "attestation_invalid",
        "coverage_reducing",
        "coverage_unproven",
        "overloaded",
        "unavailable",
    ]
    diagnostics: tuple[PolicyDiagnosticBody, ...] = ()


type ConfigRequestAdmission = (
    RoleAdmissionGranted
    | InvalidCredential
    | ForbiddenIdentity
    | AuthenticationDependencyUnavailable
)


async def admit_config_request(
    request: Request,
    dependencies: ConfigManagementRouteDependencies,
    required_roles: frozenset[ControlPlaneRole],
    *,
    mutation: bool,
) -> ConfigRequestAdmission:
    admission = await authenticate_and_admit_roles(
        request,
        authenticator=dependencies.authenticator,
        role_admission=dependencies.role_admission,
        required_roles=required_roles,
    )
    if (
        mutation
        and isinstance(admission, RoleAdmissionGranted)
        and not dependencies.mutation_admission.admits(request, admission.principal)
    ):
        return ForbiddenIdentity()
    return admission


def config_admission_error(admission: ConfigRequestAdmission) -> JSONResponse | None:
    if isinstance(admission, InvalidCredential):
        return config_error(status.HTTP_401_UNAUTHORIZED, "unauthenticated")
    if isinstance(admission, ForbiddenIdentity):
        return config_error(status.HTTP_403_FORBIDDEN, "forbidden")
    if isinstance(admission, AuthenticationDependencyUnavailable):
        return config_error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable")
    if not isinstance(admission, RoleAdmissionGranted):
        raise RuntimeError("unsupported configuration request admission")
    return None


def config_error(
    status_code: int,
    error: str,
    *,
    diagnostics: tuple[PolicyDiagnosticBody, ...] = (),
) -> JSONResponse:
    response = ConfigControlErrorBody.model_validate(
        {"ok": False, "error": error, "diagnostics": diagnostics}
    )
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers={
            **CONFIG_NO_STORE_HEADER,
            **(WWW_AUTHENTICATE_HEADER if status_code == 401 else {}),
        },
    )


def _validate_source(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_POLICY_SOURCE_BYTES:
        raise ValueError("source exceeds its UTF-8 byte limit")
    return value


def _validate_operation_id(value: str) -> str:
    if "\x00" in value or len(value.encode("utf-8")) > MAX_CONFIG_OPERATION_ID_UTF8_BYTES:
        raise ValueError("operationId exceeds its UTF-8 byte limit")
    return value
