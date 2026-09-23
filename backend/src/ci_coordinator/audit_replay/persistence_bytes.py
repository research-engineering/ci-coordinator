from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, cast

from ci_coordinator.audit_replay._event_contracts import (
    AuditEventError,
    AuditEventInput,
    AuditJsonResourceFailure,
    JsonValue,
)
from ci_coordinator.audit_replay._event_identity import audit_input_hash
from ci_coordinator.audit_replay._event_validation import (
    _audit_json_error_message,
    _audit_json_error_path,
    _audit_json_resource_failure,
    _snapshot_audit_event_input_fields,
)
from ci_coordinator.audit_replay._prepared_event import (
    PreparedAuditEvent,
    _make_prepared_audit_event,
)
from ci_coordinator.audit_replay.json_resources import AUDIT_JSON_RESOURCE_LIMITS_V1
from ci_coordinator.kernel import CanonicalJsonError, bounded_canonical_json, sha256_hex

AUDIT_PERSISTENCE_BYTE_PROFILE_SCHEMA_VERSION: Final = "ci-audit-persistence-byte-profile/v1"
AUDIT_PERSISTENCE_BYTE_PROFILE_ID: Final = "ci-audit-event-persistence-bytes/v1"
MAX_AUDIT_TEXT_UTF8_BYTES_V1: Final = 4_096
MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class AuditPersistenceByteFieldProjection:
    attribute: str
    instance_pointer: str
    storage_column: str
    measure_ref: Literal["variableText", "payload"]
    limit_ref: Literal["maxVariableTextUtf8Bytes", "maxPayloadCanonicalBytes"]
    non_empty: bool


AUDIT_PERSISTENCE_BYTE_FIELD_PROJECTIONS_V1: Final = (
    AuditPersistenceByteFieldProjection(
        "idempotency_key",
        "/idempotencyKey",
        "idempotency_key",
        "variableText",
        "maxVariableTextUtf8Bytes",
        True,
    ),
    AuditPersistenceByteFieldProjection(
        "subject_id",
        "/subjectId",
        "subject_id",
        "variableText",
        "maxVariableTextUtf8Bytes",
        True,
    ),
    AuditPersistenceByteFieldProjection(
        "event_type",
        "/eventType",
        "event_type",
        "variableText",
        "maxVariableTextUtf8Bytes",
        True,
    ),
    AuditPersistenceByteFieldProjection(
        "actor",
        "/actor",
        "actor",
        "variableText",
        "maxVariableTextUtf8Bytes",
        True,
    ),
    AuditPersistenceByteFieldProjection(
        "payload",
        "/payload",
        "payload_canonical_json",
        "payload",
        "maxPayloadCanonicalBytes",
        True,
    ),
)
AUDIT_TEXT_BYTE_FIELD_PROJECTIONS_V1: Final = tuple(
    projection
    for projection in AUDIT_PERSISTENCE_BYTE_FIELD_PROJECTIONS_V1
    if projection.measure_ref == "variableText"
)


@dataclass(frozen=True, slots=True)
class AuditPersistenceByteProfile:
    schema_version: str
    profile_id: str
    max_variable_text_utf8_bytes: int
    max_payload_canonical_bytes: int
    field_projections: tuple[AuditPersistenceByteFieldProjection, ...]


AUDIT_PERSISTENCE_BYTE_PROFILE_V1: Final = AuditPersistenceByteProfile(
    schema_version=AUDIT_PERSISTENCE_BYTE_PROFILE_SCHEMA_VERSION,
    profile_id=AUDIT_PERSISTENCE_BYTE_PROFILE_ID,
    max_variable_text_utf8_bytes=MAX_AUDIT_TEXT_UTF8_BYTES_V1,
    max_payload_canonical_bytes=MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    field_projections=AUDIT_PERSISTENCE_BYTE_FIELD_PROJECTIONS_V1,
)


AuditPersistenceByteFailure = AuditJsonResourceFailure


def prepare_audit_event(event_input: AuditEventInput) -> PreparedAuditEvent:
    snapshot = _snapshot_audit_event_input_fields(event_input)
    admit_audit_text_bytes(snapshot)
    payload_canonical_bytes = canonical_audit_payload_bytes(snapshot.payload)
    payload_snapshot = cast(JsonValue, json.loads(payload_canonical_bytes))
    payload_hash = sha256_hex(payload_canonical_bytes)
    input_hash = audit_input_hash(snapshot, payload_hash)
    return _make_prepared_audit_event(
        snapshot,
        payload_snapshot=payload_snapshot,
        payload_canonical_bytes=payload_canonical_bytes,
        payload_hash=payload_hash,
        input_hash=input_hash,
    )


def admit_audit_text_bytes(event_input: AuditEventInput) -> None:
    for projection in AUDIT_TEXT_BYTE_FIELD_PROJECTIONS_V1:
        value = getattr(event_input, projection.attribute)
        if type(value) is not str:
            continue
        if _utf8_length_exceeds(value, MAX_AUDIT_TEXT_UTF8_BYTES_V1):
            failure = AuditJsonResourceFailure(
                code="audit_text_max_utf8_bytes_exceeded",
                instance_pointer=projection.instance_pointer,
                limit=MAX_AUDIT_TEXT_UTF8_BYTES_V1,
                observed=MAX_AUDIT_TEXT_UTF8_BYTES_V1 + 1,
            )
            label = projection.instance_pointer.removeprefix("/")
            raise AuditEventError(
                f"{label} exceeds maximum UTF-8 byte count of {failure.limit}",
                resource_failure=failure,
            )


def canonical_audit_payload_bytes(value: object, *, path: str = "payload") -> bytes:
    try:
        return bounded_canonical_json(
            value,
            max_bytes=MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
            resource_limits=AUDIT_JSON_RESOURCE_LIMITS_V1,
        )
    except CanonicalJsonError as error:
        if error.code == "canonical_json_max_bytes_exceeded":
            failure = _audit_payload_byte_failure(error)
            error_path = _audit_json_error_path(error, path)
            raise AuditEventError(
                f"{error_path} exceeds maximum canonical JSON byte count of {failure.limit}",
                resource_failure=failure,
            ) from None
        resource_failure = _audit_json_resource_failure(error)
        raise AuditEventError(
            _audit_json_error_message(
                error,
                path,
                resource_failure,
            ),
            resource_failure=resource_failure,
        ) from None


def snapshot_persistable_audit_payload(value: object, *, path: str = "payload") -> JsonValue:
    canonical = canonical_audit_payload_bytes(value, path=path)
    return cast(JsonValue, json.loads(canonical))


def _audit_payload_byte_failure(error: CanonicalJsonError) -> AuditPersistenceByteFailure:
    if error.limit is None or error.observed is None:
        raise RuntimeError("canonical JSON byte failure is missing required facts") from error
    return AuditJsonResourceFailure(
        code="audit_payload_max_canonical_bytes_exceeded",
        instance_pointer="/payload",
        limit=error.limit,
        observed=error.observed,
    )


def _utf8_length_exceeds(value: str, limit: int) -> bool:
    observed = 0
    for character in value:
        code_point = ord(character)
        if code_point <= 0x7F:
            observed += 1
        elif code_point <= 0x7FF:
            observed += 2
        elif code_point <= 0xFFFF:
            observed += 3
        else:
            observed += 4
        if observed > limit:
            return True
    return False
