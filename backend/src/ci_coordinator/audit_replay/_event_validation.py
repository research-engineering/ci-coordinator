from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, cast

from ci_coordinator.audit_replay._event_contracts import (
    AUDIT_EVENT_SCHEMA_VERSION,
    AuditEventError,
    AuditEventInput,
    AuditEventRecord,
    AuditJsonResourceErrorCode,
    AuditJsonResourceFailure,
)
from ci_coordinator.audit_replay.subjects import is_audit_subject_type
from ci_coordinator.kernel import CanonicalJsonError, is_safe_json_integer

_HASH_HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_AUDIT_EVENT_ID_PATTERN: Final = re.compile(r"^audit_[0-9a-f]{32}$")
_AUDIT_TIMESTAMP_PATTERN: Final = re.compile(
    r"^(?!0000)[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_AUDIT_JSON_RESOURCE_ERROR_CODES: Final = frozenset(
    {"json_max_depth_exceeded", "json_max_nodes_exceeded"}
)


def validate_audit_event_input(event_input: AuditEventInput) -> str | None:
    idempotency_key_error = _validate_audit_text(event_input.idempotency_key, "idempotency key")
    if idempotency_key_error is not None:
        return idempotency_key_error
    if not is_audit_subject_type(event_input.subject_type):
        return "subject type is unsupported"
    subject_id_error = _validate_audit_text(event_input.subject_id, "subject id")
    if subject_id_error is not None:
        return subject_id_error
    event_type_error = _validate_audit_text(event_input.event_type, "event type")
    if event_type_error is not None:
        return event_type_error
    if not _is_iso_timestamp(event_input.created_at):
        return "createdAt must be an ISO timestamp"
    actor_error = _validate_audit_text(event_input.actor, "actor")
    if actor_error is not None:
        return actor_error
    installation_id = event_input.installation_id
    repository_id = event_input.repository_id
    if (installation_id is None) != (repository_id is None):
        return "repository scope identifiers must be both present or both absent"
    if installation_id is not None and (
        type(installation_id) is not int
        or not is_safe_json_integer(installation_id)
        or installation_id < 1
        or type(repository_id) is not int
        or not is_safe_json_integer(repository_id)
        or repository_id < 1
    ):
        return "repository scope identifiers must be positive safe integers"
    return None


def validate_audit_event_record(record: AuditEventRecord) -> str | None:
    if (
        type(record.schema_version) is not str
        or record.schema_version != AUDIT_EVENT_SCHEMA_VERSION
    ):
        return "unsupported audit event schema version"
    input_error = validate_audit_event_input(_record_input(record))
    if input_error is not None:
        return input_error
    if (
        type(record.audit_event_id) is not str
        or _AUDIT_EVENT_ID_PATTERN.fullmatch(record.audit_event_id) is None
    ):
        return "audit event id must match audit hash prefix format"
    if (
        type(record.sequence) is not int
        or not is_safe_json_integer(record.sequence)
        or record.sequence < 1
    ):
        return "sequence must be a positive safe integer"
    if record.previous_event_hash is not None and not is_hash(record.previous_event_hash):
        return "previous event hash must be null or a sha256 hex string"
    if not is_hash(record.payload_hash):
        return "payload hash must be a sha256 hex string"
    if not is_hash(record.input_hash):
        return "input hash must be a sha256 hex string"
    if not is_hash(record.event_hash):
        return "event hash must be a sha256 hex string"
    return None


def is_hash(value: object) -> bool:
    return type(value) is str and _HASH_HEX_PATTERN.fullmatch(value) is not None


def _audit_json_error_message(
    error: CanonicalJsonError,
    root_path: str,
    resource_failure: AuditJsonResourceFailure | None,
) -> str:
    path = _audit_json_error_path(error, root_path)
    messages = {
        "cycle": "must not contain cycles",
        "invalid_unicode_scalar": "must not contain invalid Unicode scalar values",
        "non_finite_number": "must not contain non-finite numbers",
        "non_string_key": "must contain only string keys",
        "unsafe_integer": "must be a JSON safe integer",
        "json_max_depth_exceeded": "exceeds maximum JSON depth",
        "json_max_nodes_exceeded": "exceeds maximum JSON node count",
    }
    reason = messages.get(error.code, "must be JSON-safe")
    if resource_failure is not None:
        reason = f"{reason} of {resource_failure.limit}"
    return f"{path} {reason}"


def _audit_json_error_path(error: CanonicalJsonError, root_path: str) -> str:
    return root_path if error.path == "$" else f"{root_path}{error.path[1:]}"


def _audit_json_resource_failure(
    error: CanonicalJsonError,
) -> AuditJsonResourceFailure | None:
    if error.code not in _AUDIT_JSON_RESOURCE_ERROR_CODES:
        return None
    if error.instance_pointer is None or error.limit is None or error.observed is None:
        raise RuntimeError("canonical JSON resource failure is missing required facts") from error
    return AuditJsonResourceFailure(
        code=cast(AuditJsonResourceErrorCode, error.code),
        instance_pointer=f"/payload{error.instance_pointer}",
        limit=error.limit,
        observed=error.observed,
    )


def _record_input(record: AuditEventRecord) -> AuditEventInput:
    return AuditEventInput(
        idempotency_key=record.idempotency_key,
        installation_id=record.installation_id,
        repository_id=record.repository_id,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        event_type=record.event_type,
        created_at=record.created_at,
        actor=record.actor,
        payload=record.payload,
    )


def _snapshot_audit_event_input_fields(event_input: AuditEventInput) -> AuditEventInput:
    if type(event_input) is not AuditEventInput:
        raise AuditEventError("audit event input must be an exact AuditEventInput")
    snapshot = AuditEventInput(
        idempotency_key=event_input.idempotency_key,
        installation_id=event_input.installation_id,
        repository_id=event_input.repository_id,
        subject_type=event_input.subject_type,
        subject_id=event_input.subject_id,
        event_type=event_input.event_type,
        created_at=event_input.created_at,
        actor=event_input.actor,
        payload=event_input.payload,
    )
    input_error = validate_audit_event_input(snapshot)
    if input_error is not None:
        raise AuditEventError(input_error)
    return snapshot


def _is_non_empty_string(value: object) -> bool:
    return type(value) is str and value != ""


def _validate_audit_text(value: object, label: str) -> str | None:
    if not _is_non_empty_string(value):
        return f"{label} must be a non-empty string"
    if any(0xD800 <= ord(character) <= 0xDFFF for character in cast(str, value)):
        return f"{label} must not contain invalid Unicode scalar values"
    return None


def _is_iso_timestamp(value: object) -> bool:
    if type(value) is not str or _AUDIT_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return False
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") == value


def _normalize_audit_sequence(value: object) -> int:
    if type(value) is int:
        normalized = value
    elif type(value) is float and is_safe_json_integer(value):
        normalized = int(value)
    else:
        raise AuditEventError("sequence must be a positive safe integer")
    if normalized < 1 or not is_safe_json_integer(normalized):
        raise AuditEventError("sequence must be a positive safe integer")
    return normalized
