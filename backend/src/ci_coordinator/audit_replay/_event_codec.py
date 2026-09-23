from __future__ import annotations

from collections.abc import Mapping
from itertools import islice
from typing import Final, Literal, NoReturn, cast

from ci_coordinator.audit_replay._event_contracts import (
    AUDIT_EVENT_SCHEMA_VERSION,
    AuditEventError,
    AuditEventInput,
    AuditEventRecord,
    AuditEventRecordLike,
    JsonValue,
)
from ci_coordinator.audit_replay._event_validation import (
    _normalize_audit_sequence,
    _record_input,
    validate_audit_event_input,
    validate_audit_event_record,
)
from ci_coordinator.audit_replay._prepared_event import _audit_event_input_from_prepared
from ci_coordinator.audit_replay.persistence_bytes import (
    admit_audit_text_bytes,
    prepare_audit_event,
    snapshot_persistable_audit_payload,
)
from ci_coordinator.audit_replay.subjects import AuditSubjectType, is_audit_subject_type

_UNSCOPED_AUDIT_EVENT_INPUT_KEY_ORDER: Final = (
    "idempotencyKey",
    "subjectType",
    "subjectId",
    "eventType",
    "createdAt",
    "actor",
    "payload",
)
_SCOPED_AUDIT_EVENT_INPUT_KEY_ORDER: Final = (
    "idempotencyKey",
    "installationId",
    "repositoryId",
    *_UNSCOPED_AUDIT_EVENT_INPUT_KEY_ORDER[1:],
)
_AUDIT_EVENT_INPUT_KEYS: Final = frozenset(_SCOPED_AUDIT_EVENT_INPUT_KEY_ORDER)
_AUDIT_EVENT_RECORD_SUFFIX: Final = (
    "schemaVersion",
    "auditEventId",
    "sequence",
    "previousEventHash",
    "payloadHash",
    "inputHash",
    "eventHash",
)
_UNSCOPED_AUDIT_EVENT_RECORD_KEY_ORDER: Final = (
    *_UNSCOPED_AUDIT_EVENT_INPUT_KEY_ORDER,
    *_AUDIT_EVENT_RECORD_SUFFIX,
)
_SCOPED_AUDIT_EVENT_RECORD_KEY_ORDER: Final = (
    *_SCOPED_AUDIT_EVENT_INPUT_KEY_ORDER,
    *_AUDIT_EVENT_RECORD_SUFFIX,
)
_AUDIT_EVENT_RECORD_KEY_SETS: Final = frozenset(
    {
        frozenset(_UNSCOPED_AUDIT_EVENT_RECORD_KEY_ORDER),
        frozenset(_SCOPED_AUDIT_EVENT_RECORD_KEY_ORDER),
    }
)


def audit_event_from_mapping(record: AuditEventRecordLike | None) -> AuditEventRecord:
    if record is None:
        raise AuditEventError("audit event record is missing")
    if type(record) is AuditEventRecord:
        return _validated_record(record)
    if isinstance(record, AuditEventRecord):
        raise AuditEventError("audit event record must be an exact AuditEventRecord")
    if not isinstance(record, Mapping):
        raise AuditEventError("audit event record must be an object")

    snapshot = _snapshot_mapping_shape(
        record,
        (
            _UNSCOPED_AUDIT_EVENT_RECORD_KEY_ORDER,
            _SCOPED_AUDIT_EVENT_RECORD_KEY_ORDER,
        ),
        "audit event record",
        diagnostic_key="auditEventId",
    )
    diagnostic_id = _audit_event_id_from_snapshot(snapshot)
    try:
        return _audit_event_from_snapshot(snapshot)
    except AuditEventError as error:
        raise AuditEventError(
            str(error),
            audit_event_id=diagnostic_id,
            resource_failure=error.resource_failure,
        ) from None


def _audit_event_from_snapshot(snapshot: dict[str, object]) -> AuditEventRecord:
    payload = snapshot_json_value(_require_field(snapshot, "payload"))
    schema_version = _require_literal(
        snapshot,
        "schemaVersion",
        AUDIT_EVENT_SCHEMA_VERSION,
        "unsupported audit event schema version",
    )
    subject_type = _require_subject_type(snapshot, "subjectType")
    result = AuditEventRecord(
        idempotency_key=_require_str(snapshot, "idempotencyKey"),
        installation_id=_require_optional_positive_int(snapshot, "installationId"),
        repository_id=_require_optional_positive_int(snapshot, "repositoryId"),
        subject_type=subject_type,
        subject_id=_require_str(snapshot, "subjectId"),
        event_type=_require_str(snapshot, "eventType"),
        created_at=_require_str(snapshot, "createdAt"),
        actor=_require_str(snapshot, "actor"),
        payload=payload,
        schema_version=schema_version,
        audit_event_id=_require_str(snapshot, "auditEventId"),
        sequence=_require_audit_sequence(snapshot),
        previous_event_hash=_require_optional_str(snapshot, "previousEventHash"),
        payload_hash=_require_str(snapshot, "payloadHash"),
        input_hash=_require_str(snapshot, "inputHash"),
        event_hash=_require_str(snapshot, "eventHash"),
    )
    input_error = validate_audit_event_input(_record_input(result))
    if input_error is not None:
        raise AuditEventError(input_error)
    _admit_audit_text_bytes(_record_input(result))
    record_error = validate_audit_event_record(result)
    if record_error is not None:
        raise AuditEventError(record_error)
    return _validated_record(result)


def audit_event_to_mapping(record: AuditEventRecord) -> dict[str, object]:
    result: dict[str, object] = {
        "idempotencyKey": record.idempotency_key,
        "subjectType": record.subject_type,
        "subjectId": record.subject_id,
        "eventType": record.event_type,
        "createdAt": record.created_at,
        "actor": record.actor,
        "payload": snapshot_json_value(record.payload),
        "schemaVersion": record.schema_version,
        "auditEventId": record.audit_event_id,
        "sequence": record.sequence,
        "previousEventHash": record.previous_event_hash,
        "payloadHash": record.payload_hash,
        "inputHash": record.input_hash,
        "eventHash": record.event_hash,
    }
    if record.installation_id is not None:
        result["installationId"] = record.installation_id
        result["repositoryId"] = record.repository_id
    return result


def audit_event_input_from_mapping(data: Mapping[str, object]) -> AuditEventInput:
    snapshot = _snapshot_mapping_shape(
        data,
        (
            _UNSCOPED_AUDIT_EVENT_INPUT_KEY_ORDER,
            _SCOPED_AUDIT_EVENT_INPUT_KEY_ORDER,
            _UNSCOPED_AUDIT_EVENT_RECORD_KEY_ORDER,
            _SCOPED_AUDIT_EVENT_RECORD_KEY_ORDER,
        ),
        "audit event input",
    )
    if frozenset(snapshot) in _AUDIT_EVENT_RECORD_KEY_SETS:
        return _record_input(audit_event_from_mapping(snapshot))
    return snapshot_audit_event_input(
        AuditEventInput(
            idempotency_key=_require_str(snapshot, "idempotencyKey"),
            installation_id=_require_optional_positive_int(snapshot, "installationId"),
            repository_id=_require_optional_positive_int(snapshot, "repositoryId"),
            subject_type=_require_subject_type(snapshot, "subjectType"),
            subject_id=_require_str(snapshot, "subjectId"),
            event_type=_require_str(snapshot, "eventType"),
            created_at=_require_str(snapshot, "createdAt"),
            actor=_require_str(snapshot, "actor"),
            payload=cast(JsonValue, _require_field(snapshot, "payload")),
        )
    )


def snapshot_json_value(
    value: object,
    path: str = "payload",
) -> JsonValue:
    return snapshot_persistable_audit_payload(value, path=path)


def snapshot_audit_event_input(event_input: AuditEventInput) -> AuditEventInput:
    return _audit_event_input_from_prepared(prepare_audit_event(event_input))


def _validated_record(record: AuditEventRecord) -> AuditEventRecord:
    if type(record) is not AuditEventRecord:
        raise AuditEventError("audit event record must be an exact AuditEventRecord")
    diagnostic_id = record.audit_event_id if type(record.audit_event_id) is str else None
    try:
        return _validated_record_snapshot(record)
    except AuditEventError as error:
        raise AuditEventError(
            str(error),
            audit_event_id=diagnostic_id,
            resource_failure=error.resource_failure,
        ) from None


def _validated_record_snapshot(record: AuditEventRecord) -> AuditEventRecord:
    payload = snapshot_json_value(record.payload)
    sequence = _normalize_audit_sequence(record.sequence)
    result = AuditEventRecord(
        idempotency_key=record.idempotency_key,
        installation_id=record.installation_id,
        repository_id=record.repository_id,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        event_type=record.event_type,
        created_at=record.created_at,
        actor=record.actor,
        payload=payload,
        schema_version=record.schema_version,
        audit_event_id=record.audit_event_id,
        sequence=sequence,
        previous_event_hash=record.previous_event_hash,
        payload_hash=record.payload_hash,
        input_hash=record.input_hash,
        event_hash=record.event_hash,
    )
    input_error = validate_audit_event_input(_record_input(result))
    if input_error is not None:
        raise AuditEventError(input_error)
    _admit_audit_text_bytes(_record_input(result))
    record_error = validate_audit_event_record(result)
    if record_error is not None:
        raise AuditEventError(record_error)
    return result


def _admit_audit_text_bytes(event_input: AuditEventInput) -> None:
    admit_audit_text_bytes(event_input)


def _require_field(data: Mapping[str, object], key: str) -> object:
    try:
        return data[key]
    except KeyError:
        raise AuditEventError(f"audit event record is missing {key}") from None


def _require_optional_positive_int(data: Mapping[str, object], key: str) -> int | None:
    if key not in data:
        return None
    value = _require_field(data, key)
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise AuditEventError(f"{key} must be null or a positive integer")
    return value


def _snapshot_mapping_shape(
    data: Mapping[str, object],
    expected_shapes: tuple[tuple[str, ...], ...],
    label: str,
    *,
    diagnostic_key: str | None = None,
) -> dict[str, object]:
    max_key_count = max(len(shape) for shape in expected_shapes)
    try:
        keys = tuple(islice(iter(data), max_key_count + 1))
    except Exception:
        raise AuditEventError(f"{label} could not be snapshotted") from None
    seen: set[str] = set()
    for key in keys:
        if type(key) is not str:
            _raise_mapping_shape_error(
                data, keys, f"{label} contains unknown key {key}", diagnostic_key
            )
        if key in seen:
            _raise_mapping_shape_error(
                data, keys, f"{label} contains duplicate key {key}", diagnostic_key
            )
        seen.add(key)

    if len(keys) > max_key_count:
        known_keys = frozenset(key for shape in expected_shapes for key in shape)
        unknown_key = next((key for key in keys if key not in known_keys), None)
        message = (
            f"{label} contains unknown key {unknown_key}"
            if unknown_key is not None
            else f"{label} contains more than {max_key_count} keys"
        )
        _raise_mapping_shape_error(data, keys, message, diagnostic_key)

    expected_order = next(
        (shape for shape in expected_shapes if seen == frozenset(shape)),
        None,
    )
    if expected_order is None:
        known_keys = frozenset(key for shape in expected_shapes for key in shape)
        unknown_key = next((key for key in keys if key not in known_keys), None)
        if unknown_key is not None:
            _raise_mapping_shape_error(
                data,
                keys,
                f"{label} contains unknown key {unknown_key}",
                diagnostic_key,
            )
        preferred_shape = (
            expected_shapes[-1] if seen - _AUDIT_EVENT_INPUT_KEYS else expected_shapes[0]
        )
        missing_key = next(key for key in preferred_shape if key not in seen)
        _raise_mapping_shape_error(
            data,
            keys,
            f"{label} is missing {missing_key}",
            diagnostic_key,
        )

    read_order = expected_order
    if diagnostic_key is not None:
        read_order = (diagnostic_key, *(key for key in expected_order if key != diagnostic_key))
    snapshot: dict[str, object] = {}
    for key in read_order:
        try:
            snapshot[key] = data[key]
        except Exception:
            diagnostic_id = _audit_event_id_from_snapshot(snapshot)
            raise AuditEventError(
                f"{label}.{key} could not be snapshotted",
                audit_event_id=diagnostic_id,
            ) from None
    return snapshot


def _raise_mapping_shape_error(
    data: Mapping[str, object],
    keys: tuple[str, ...],
    message: str,
    diagnostic_key: str | None,
) -> NoReturn:
    diagnostic_id: str | None = None
    if diagnostic_key is not None and diagnostic_key in keys:
        try:
            value = data[diagnostic_key]
        except Exception:
            value = None
        if type(value) is str:
            diagnostic_id = value
    raise AuditEventError(message, audit_event_id=diagnostic_id)


def _audit_event_id_from_snapshot(snapshot: dict[str, object]) -> str | None:
    value = snapshot.get("auditEventId")
    return value if type(value) is str else None


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = _require_field(data, key)
    if type(value) is not str:
        raise AuditEventError(f"{key} must be a string")
    return value


def _require_optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = _require_field(data, key)
    if value is None:
        return None
    if type(value) is not str:
        raise AuditEventError(f"{key} must be null or a string")
    return value


def _require_audit_sequence(data: Mapping[str, object]) -> int:
    return _normalize_audit_sequence(_require_field(data, "sequence"))


def _require_subject_type(data: Mapping[str, object], key: str) -> AuditSubjectType:
    value = _require_str(data, key)
    if not is_audit_subject_type(value):
        raise AuditEventError("subject type is unsupported")
    return value


def _require_literal(
    data: Mapping[str, object],
    key: str,
    expected: Literal["ci-audit-event/v1"],
    message: str,
) -> Literal["ci-audit-event/v1"]:
    value = _require_str(data, key)
    if value != expected:
        raise AuditEventError(message)
    return expected
