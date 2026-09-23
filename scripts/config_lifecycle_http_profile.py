"""Exact admission and OpenAPI projection for the config lifecycle HTTP profile."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ci_coordinator.config_control.limits import (
    MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
    MAX_POLICY_SOURCE_BYTES,
)
from ci_coordinator.config_epochs import (
    MAX_CONFIG_EPOCH_PAGE_SIZE,
    MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
    MAX_ROLLBACK_REASON_UTF8_BYTES,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from scripts.proofkit_common import JsonObject, as_array, as_object
from scripts.repository_paths import read_repository_regular_file

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH = Path(
    "docs/specs/ci-coordinator-control-plane/config-lifecycle-http-profile.v1.json"
)
MAX_PROFILE_BYTES: Final = 64 * 1024

_OPERATION_FIELDS = {
    "effect",
    "httpOperationId",
    "method",
    "path",
    "requestSchema",
    "requiredRoles",
    "semanticOperationId",
    "successSchema",
}
_EXPECTED_INVARIANTS = (
    "operation-effect-classification-begins-after-transport-request-admission",
    "authentication-and-role-admission-precede-use-case-execution",
    "scope-admission-precedes-row-projection",
    "validation-has-no-durable-or-provider-effect",
    "registration-replay-compares-all-client-owned-facts",
    "status-cardinality-is-page-bounded",
    "export-revalidates-and-emits-exact-retained-bytes",
    "success-projections-do-not-widen-runtime-value-domains",
)
_EXPECTED_NON_CLAIMS = (
    "This profile does not define every CI Coordinator API operation or establish UI parity.",
    (
        "This profile does not prove provider deployment, production identity installation, "
        "retention, backup, failover, or dynamic CI omission."
    ),
)


@dataclass(frozen=True, slots=True)
class ConfigLifecycleHttpOperation:
    semantic_operation_id: str
    http_operation_id: str
    method: str
    path: str
    required_roles: tuple[str, ...]
    effect: str
    request_schema: str | None
    success_schema: str


@dataclass(frozen=True, slots=True)
class ConfigLifecycleHttpProfile:
    maximum_contract_identity_bytes: int
    maximum_json_safe_integer: int
    maximum_page_size: int
    maximum_policy_source_bytes: int
    maximum_operation_id_bytes: int
    maximum_rollback_reason_bytes: int
    operations: tuple[ConfigLifecycleHttpOperation, ...]


_EXPECTED_OPERATIONS = (
    ConfigLifecycleHttpOperation(
        "validate-configuration",
        "validate_config_epoch",
        "POST",
        "/api/v1/config/validations",
        ("configure",),
        "pure",
        "ci-config-epoch-validation/v1",
        "ci-config-epoch-validation-result/v1",
    ),
    ConfigLifecycleHttpOperation(
        "register-configuration",
        "register_config_epoch",
        "POST",
        "/api/v1/config/epochs",
        ("configure",),
        "postgresql-audit",
        "ci-config-epoch-registration/v1",
        "ci-config-epoch-registration-result/v1",
    ),
    ConfigLifecycleHttpOperation(
        "activate-configuration",
        "activate_config_epoch",
        "POST",
        "/api/v1/config/activations",
        ("activate",),
        "postgresql-audit-provider-evidence",
        "ci-config-epoch-activation/v1",
        "ci-config-epoch-activation-result/v1",
    ),
    ConfigLifecycleHttpOperation(
        "rollback-configuration",
        "rollback_config_epoch",
        "POST",
        "/api/v1/config/rollbacks",
        ("activate",),
        "postgresql-audit",
        "ci-config-epoch-rollback/v1",
        "ci-config-epoch-activation-result/v1",
    ),
    ConfigLifecycleHttpOperation(
        "read-configuration-status",
        "get_config_epoch_status",
        "GET",
        "/api/v1/config/repositories/{installation_id}/{repository_id}/status",
        ("configure",),
        "bounded-postgresql-read",
        None,
        "ci-config-epoch-status/v1",
    ),
    ConfigLifecycleHttpOperation(
        "export-configuration-source",
        "export_config_epoch_source",
        "GET",
        ("/api/v1/config/repositories/{installation_id}/{repository_id}/epochs/{epoch_id}/source"),
        ("configure",),
        "exact-postgresql-read",
        None,
        "exact-retained-source-bytes",
    ),
)


def load_config_lifecycle_http_profile(
    repo_root: Path = REPO_ROOT,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> ConfigLifecycleHttpProfile:
    payload = read_repository_regular_file(
        repo_root,
        profile_path,
        "config lifecycle HTTP profile",
        maximum_bytes=MAX_PROFILE_BYTES,
    )
    document = _parse_document(payload)
    _exact_keys(
        document,
        {
            "bounds",
            "invariants",
            "nonClaims",
            "operations",
            "ownerId",
            "profileId",
            "schemaVersion",
        },
        "config lifecycle HTTP profile",
    )
    _literal(document, "schemaVersion", "ci-config-lifecycle-http-profile/v1")
    _literal(document, "profileId", "ci-config-lifecycle-http/v1")
    _literal(document, "ownerId", "ci-coordinator.control-plane")

    bounds = as_object(document.get("bounds"), "config lifecycle bounds")
    _exact_keys(
        bounds,
        {
            "maximumContractIdentityBytes",
            "maximumJsonSafeInteger",
            "maximumOperationIdBytes",
            "maximumPageSize",
            "maximumPolicySourceBytes",
            "maximumRollbackReasonBytes",
        },
        "config lifecycle bounds",
    )
    expected_bounds = {
        "maximumContractIdentityBytes": MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
        "maximumJsonSafeInteger": MAX_SAFE_JSON_INTEGER,
        "maximumPageSize": MAX_CONFIG_EPOCH_PAGE_SIZE,
        "maximumPolicySourceBytes": MAX_POLICY_SOURCE_BYTES,
        "maximumOperationIdBytes": MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        "maximumRollbackReasonBytes": MAX_ROLLBACK_REASON_UTF8_BYTES,
    }
    if bounds != expected_bounds or any(type(value) is not int for value in bounds.values()):
        raise ValueError("config lifecycle bounds differ from runtime owners")

    operations = tuple(
        _operation(as_object(value, "config lifecycle operation"))
        for value in as_array(document.get("operations"), "config lifecycle operations")
    )
    if operations != _EXPECTED_OPERATIONS:
        raise ValueError("config lifecycle operations differ from the admitted contract")
    if len({(item.method, item.path) for item in operations}) != len(operations):
        raise ValueError("config lifecycle operation coordinates must be unique")
    if len({item.http_operation_id for item in operations}) != len(operations):
        raise ValueError("config lifecycle HTTP operation identities must be unique")
    if len({item.semantic_operation_id for item in operations}) != len(operations):
        raise ValueError("config lifecycle semantic operation identities must be unique")

    if _string_tuple(document.get("invariants"), "config lifecycle invariants") != (
        _EXPECTED_INVARIANTS
    ):
        raise ValueError("config lifecycle invariants differ from the admitted contract")
    if _string_tuple(document.get("nonClaims"), "config lifecycle non-claims") != (
        _EXPECTED_NON_CLAIMS
    ):
        raise ValueError("config lifecycle non-claims differ from the admitted contract")

    return ConfigLifecycleHttpProfile(
        maximum_contract_identity_bytes=MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
        maximum_json_safe_integer=MAX_SAFE_JSON_INTEGER,
        maximum_page_size=MAX_CONFIG_EPOCH_PAGE_SIZE,
        maximum_policy_source_bytes=MAX_POLICY_SOURCE_BYTES,
        maximum_operation_id_bytes=MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        maximum_rollback_reason_bytes=MAX_ROLLBACK_REASON_UTF8_BYTES,
        operations=operations,
    )


def assert_config_lifecycle_openapi(
    document: JsonObject,
    profile: ConfigLifecycleHttpProfile,
) -> None:
    paths = as_object(document.get("paths"), "OpenAPI paths")
    expected_coordinates = {(item.method.lower(), item.path) for item in profile.operations}
    observed_coordinates = {
        (method, path)
        for path, raw_path_item in paths.items()
        if path.startswith("/api/v1/config/")
        for method in as_object(raw_path_item, f"OpenAPI path {path}")
    }
    if observed_coordinates != expected_coordinates:
        raise ValueError("config lifecycle OpenAPI route set differs from its profile")

    for expected in profile.operations:
        operation = as_object(
            as_object(paths.get(expected.path), f"OpenAPI path {expected.path}").get(
                expected.method.lower()
            ),
            f"OpenAPI operation {expected.http_operation_id}",
        )
        if operation.get("operationId") != expected.http_operation_id:
            raise ValueError("config lifecycle OpenAPI operation identity differs from its profile")
        _assert_request_schema(document, operation, expected)
        _assert_success_schema(document, operation, expected)
    _assert_openapi_bounds(document, profile)


def _operation(value: JsonObject) -> ConfigLifecycleHttpOperation:
    _exact_keys(value, _OPERATION_FIELDS, "config lifecycle operation")
    request_schema = value.get("requestSchema")
    if request_schema is not None and type(request_schema) is not str:
        raise ValueError("config lifecycle request schema must be text or null")
    return ConfigLifecycleHttpOperation(
        semantic_operation_id=_text(value.get("semanticOperationId"), "semantic operation id"),
        http_operation_id=_text(value.get("httpOperationId"), "HTTP operation id"),
        method=_text(value.get("method"), "HTTP method"),
        path=_text(value.get("path"), "HTTP path"),
        required_roles=_string_tuple(value.get("requiredRoles"), "required roles"),
        effect=_text(value.get("effect"), "operation effect"),
        request_schema=request_schema,
        success_schema=_text(value.get("successSchema"), "success schema"),
    )


def _assert_request_schema(
    document: JsonObject,
    operation: JsonObject,
    expected: ConfigLifecycleHttpOperation,
) -> None:
    request_body = operation.get("requestBody")
    if expected.request_schema is None:
        if request_body is not None:
            raise ValueError("config lifecycle GET operation unexpectedly has a request body")
        return
    body = as_object(request_body, "OpenAPI request body")
    content = as_object(body.get("content"), "OpenAPI request content")
    media = as_object(content.get("application/json"), "OpenAPI JSON request content")
    schema = _resolve_schema(document, media.get("schema"))
    if _schema_version(schema) != expected.request_schema:
        raise ValueError("config lifecycle request schema differs from its profile")


def _assert_success_schema(
    document: JsonObject,
    operation: JsonObject,
    expected: ConfigLifecycleHttpOperation,
) -> None:
    responses = as_object(operation.get("responses"), "OpenAPI operation responses")
    successful = [
        as_object(response, f"OpenAPI response {status_code}")
        for status_code, response in responses.items()
        if status_code.startswith("2")
    ]
    if not successful:
        raise ValueError("config lifecycle operation has no success response")
    if expected.success_schema == "exact-retained-source-bytes":
        for response in successful:
            content = as_object(response.get("content"), "exact source response content")
            if set(content) != {"application/json", "application/yaml"}:
                raise ValueError("exact source media types differ from the profile")
        return
    for response in successful:
        content = as_object(response.get("content"), "OpenAPI success content")
        media = as_object(content.get("application/json"), "OpenAPI JSON success content")
        schema = _resolve_schema(document, media.get("schema"))
        if _schema_version(schema) != expected.success_schema:
            raise ValueError("config lifecycle success schema differs from its profile")


def _assert_openapi_bounds(
    document: JsonObject,
    profile: ConfigLifecycleHttpProfile,
) -> None:
    for schema_name in (
        "ConfigEpochActivationBody",
        "ConfigEpochRegistrationBody",
        "ConfigEpochRollbackBody",
    ):
        schema = _component_schema(document, schema_name)
        properties = as_object(schema.get("properties"), f"{schema_name} properties")
        operation_id = as_object(properties.get("operationId"), f"{schema_name}.operationId")
        if (
            operation_id.get("maxLength") != profile.maximum_operation_id_bytes
            or operation_id.get("x-max-utf8-bytes") != profile.maximum_operation_id_bytes
        ):
            raise ValueError("config operation identity bound differs from its profile")

    for schema_name in ("ConfigEpochRegistrationBody", "ConfigEpochValidationBody"):
        schema = _component_schema(document, schema_name)
        properties = as_object(schema.get("properties"), f"{schema_name} properties")
        source = as_object(properties.get("source"), f"{schema_name}.source")
        if (
            source.get("maxLength") != profile.maximum_policy_source_bytes
            or source.get("x-max-utf8-bytes") != profile.maximum_policy_source_bytes
        ):
            raise ValueError("config policy source bound differs from its profile")

    rollback = _component_schema(document, "ConfigEpochRollbackBody")
    rollback_properties = as_object(
        rollback.get("properties"), "ConfigEpochRollbackBody properties"
    )
    rollback_reason = as_object(rollback_properties.get("reason"), "ConfigEpochRollbackBody.reason")
    if (
        rollback_reason.get("maxLength") != profile.maximum_rollback_reason_bytes
        or rollback_reason.get("x-max-utf8-bytes") != profile.maximum_rollback_reason_bytes
    ):
        raise ValueError("config rollback reason bound differs from its profile")

    hash_fields = {
        "ActiveConfigEpochBody": ("epochId",),
        "ConfigActivationAcceptedBody": ("epochId",),
        "ConfigEpochAcceptedBody": ("epochId",),
        "ConfigEpochSummaryBody": (
            "epochId",
            "sourceHash",
            "documentHash",
            "epochHash",
        ),
        "ConfigEpochValidationAcceptedBody": (
            "epochId",
            "sourceHash",
            "documentHash",
            "epochHash",
        ),
    }
    for schema_name, field_names in hash_fields.items():
        for field_name in field_names:
            _assert_sha256_schema(
                _property_schema(document, schema_name, field_name),
                f"{schema_name}.{field_name}",
            )

    for field_name in (
        "documentSchemaId",
        "documentProfileId",
        "semanticProfileId",
        "compiledSchemaId",
    ):
        identity = _property_schema(
            document,
            "ConfigEpochValidationAcceptedBody",
            field_name,
        )
        if (
            identity.get("type") != "string"
            or identity.get("minLength") != 1
            or identity.get("maxLength") != profile.maximum_contract_identity_bytes
            or identity.get("x-max-utf8-bytes") != profile.maximum_contract_identity_bytes
        ):
            raise ValueError("config contract identity bound differs from its profile")

    positive_integer_fields = {
        "ActiveConfigEpochBody": ("revision",),
        "ConfigActivationAcceptedBody": ("revision",),
        "ConfigEpochStatusBody": ("installationId", "repositoryId"),
        "ConfigEpochValidationAcceptedBody": ("installationId", "repositoryId"),
    }
    for schema_name, field_names in positive_integer_fields.items():
        for field_name in field_names:
            integer = _property_schema(document, schema_name, field_name)
            if (
                integer.get("type") != "integer"
                or integer.get("minimum") != 1
                or integer.get("maximum") != profile.maximum_json_safe_integer
            ):
                raise ValueError("config response safe-integer bound differs from its profile")

    source_byte_count = _property_schema(
        document,
        "ConfigEpochSummaryBody",
        "sourceByteCount",
    )
    if (
        source_byte_count.get("type") != "integer"
        or source_byte_count.get("minimum") != 1
        or source_byte_count.get("maximum") != profile.maximum_policy_source_bytes
    ):
        raise ValueError("config source byte-count bound differs from its profile")

    paths = as_object(document.get("paths"), "OpenAPI paths")
    status_operation = as_object(
        as_object(
            paths.get("/api/v1/config/repositories/{installation_id}/{repository_id}/status"),
            "config status path",
        ).get("get"),
        "config status operation",
    )
    parameters = as_array(status_operation.get("parameters"), "config status parameters")
    limit = next(
        (
            as_object(parameter, "config status parameter")
            for parameter in parameters
            if as_object(parameter, "config status parameter").get("name") == "limit"
        ),
        None,
    )
    if limit is None:
        raise ValueError("config status limit parameter is missing")
    limit_schema = as_object(limit.get("schema"), "config status limit schema")
    if limit_schema.get("maximum") != profile.maximum_page_size:
        raise ValueError("config status page bound differs from its profile")

    status_schema = _component_schema(document, "ConfigEpochStatusBody")
    status_properties = as_object(
        status_schema.get("properties"), "ConfigEpochStatusBody properties"
    )
    epochs = as_object(status_properties.get("epochs"), "ConfigEpochStatusBody.epochs")
    if epochs.get("maxItems") != profile.maximum_page_size:
        raise ValueError("config status response bound differs from its profile")

    next_cursor = _property_schema(document, "ConfigEpochStatusBody", "nextCursor", resolve=False)
    alternatives = as_array(next_cursor.get("anyOf"), "ConfigEpochStatusBody.nextCursor anyOf")
    resolved_alternatives = tuple(_resolve_schema(document, value) for value in alternatives)
    non_null = tuple(value for value in resolved_alternatives if value.get("type") != "null")
    null_count = sum(value.get("type") == "null" for value in resolved_alternatives)
    if len(resolved_alternatives) != 2 or len(non_null) != 1 or null_count != 1:
        raise ValueError("config status cursor nullability differs from its profile")
    _assert_sha256_schema(non_null[0], "ConfigEpochStatusBody.nextCursor")


def _property_schema(
    document: JsonObject,
    schema_name: str,
    field_name: str,
    *,
    resolve: bool = True,
) -> JsonObject:
    schema = _component_schema(document, schema_name)
    properties = as_object(schema.get("properties"), f"{schema_name} properties")
    raw = properties.get(field_name)
    return (
        _resolve_schema(document, raw) if resolve else as_object(raw, f"{schema_name}.{field_name}")
    )


def _assert_sha256_schema(schema: JsonObject, label: str) -> None:
    if (
        schema.get("type") != "string"
        or schema.get("minLength") != 64
        or schema.get("maxLength") != 64
        or schema.get("pattern") != r"^[0-9a-f]{64}$"
    ):
        raise ValueError(f"{label} SHA-256 contract differs from its profile")


def _resolve_schema(document: JsonObject, raw_schema: object) -> JsonObject:
    schema = as_object(raw_schema, "OpenAPI schema")
    reference = schema.get("$ref")
    if type(reference) is not str or not reference.startswith("#/components/schemas/"):
        return schema
    return _component_schema(document, reference.rsplit("/", maxsplit=1)[-1])


def _component_schema(document: JsonObject, name: str) -> JsonObject:
    components = as_object(document.get("components"), "OpenAPI components")
    schemas = as_object(components.get("schemas"), "OpenAPI component schemas")
    return as_object(schemas.get(name), f"OpenAPI component schema {name}")


def _schema_version(schema: JsonObject) -> object:
    properties = as_object(schema.get("properties"), "versioned schema properties")
    version = as_object(properties.get("schemaVersion"), "schemaVersion property")
    return version.get("const")


def _parse_document(payload: bytes) -> JsonObject:
    try:
        value: object = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("config lifecycle HTTP profile is not strict JSON") from error
    return as_object(value, "config lifecycle HTTP profile")


def _unique_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _exact_keys(value: JsonObject, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields differ from the admitted contract")


def _literal(value: JsonObject, field: str, expected: object) -> None:
    if type(value.get(field)) is not type(expected) or value.get(field) != expected:
        raise ValueError(f"config lifecycle {field} differs from the admitted contract")


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    items = as_array(value, label)
    if any(type(item) is not str or not item for item in items):
        raise ValueError(f"{label} must contain non-empty text")
    return tuple(items)
