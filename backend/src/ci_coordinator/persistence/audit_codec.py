from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from ci_coordinator.audit_replay import (
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
    AuditEventRecord,
    PreparedAuditEvent,
)
from ci_coordinator.audit_replay.event import audit_event_from_mapping
from ci_coordinator.audit_replay.persistence_bytes import canonical_audit_payload_bytes
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.scalar_rows import required_bytes as _bytes_field
from ci_coordinator.persistence.scalar_rows import required_int as _integer_field
from ci_coordinator.persistence.scalar_rows import required_string as _text_field


def idempotency_key_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def prepared_record_to_row(
    record: AuditEventRecord,
    prepared: PreparedAuditEvent,
) -> dict[str, object]:
    _require_prepared_record_identity(record, prepared)
    return {
        "sequence": record.sequence,
        "installation_id": record.installation_id,
        "repository_id": record.repository_id,
        "idempotency_key": prepared.idempotency_key.encode("utf-8"),
        "idempotency_key_digest": idempotency_key_digest(prepared.idempotency_key),
        "subject_type": record.subject_type,
        "subject_id": prepared.subject_id.encode("utf-8"),
        "event_type": prepared.event_type.encode("utf-8"),
        "created_at": record.created_at,
        "actor": prepared.actor.encode("utf-8"),
        "payload_canonical_json": prepared.payload_canonical_bytes,
        "schema_version": record.schema_version,
        "audit_event_id": record.audit_event_id,
        "previous_event_hash": _optional_hash_bytes(record.previous_event_hash),
        "payload_hash": bytes.fromhex(record.payload_hash),
        "input_hash": bytes.fromhex(record.input_hash),
        "event_hash": bytes.fromhex(record.event_hash),
    }


def row_to_record(row: Mapping[str, object]) -> AuditEventRecord:
    try:
        payload_bytes = _bounded_bytes_field(
            row,
            "payload_canonical_json",
            MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
        )
        idempotency_key_bytes = _bounded_bytes_field(
            row,
            "idempotency_key",
            MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        )
        subject_id_bytes = _bounded_bytes_field(
            row,
            "subject_id",
            MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        )
        event_type_bytes = _bounded_bytes_field(
            row,
            "event_type",
            MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        )
        actor_bytes = _bounded_bytes_field(
            row,
            "actor",
            MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        )
        payload = json.loads(payload_bytes.decode("utf-8"))
        if canonical_audit_payload_bytes(payload) != payload_bytes:
            raise ValueError("payload bytes are not canonical JSON")
        idempotency_key = idempotency_key_bytes.decode("utf-8")
        if _bytes_field(row, "idempotency_key_digest") != idempotency_key_digest(idempotency_key):
            raise ValueError("idempotency key digest does not match stored key")
        return audit_event_from_mapping(
            {
                "idempotencyKey": idempotency_key,
                "installationId": _optional_positive_integer_field(row, "installation_id"),
                "repositoryId": _optional_positive_integer_field(row, "repository_id"),
                "subjectType": _text_field(row, "subject_type"),
                "subjectId": subject_id_bytes.decode("utf-8"),
                "eventType": event_type_bytes.decode("utf-8"),
                "createdAt": _text_field(row, "created_at"),
                "actor": actor_bytes.decode("utf-8"),
                "payload": payload,
                "schemaVersion": _text_field(row, "schema_version"),
                "auditEventId": _text_field(row, "audit_event_id"),
                "sequence": _integer_field(row, "sequence"),
                "previousEventHash": _optional_hash_hex(row, "previous_event_hash"),
                "payloadHash": _hash_hex(row, "payload_hash"),
                "inputHash": _hash_hex(row, "input_hash"),
                "eventHash": _hash_hex(row, "event_hash"),
            }
        )
    except (KeyError, RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise PersistenceInvariantViolation("stored audit event is invalid") from error


def _require_prepared_record_identity(
    record: AuditEventRecord,
    prepared: PreparedAuditEvent,
) -> None:
    if type(prepared) is not PreparedAuditEvent:
        raise PersistenceInvariantViolation("audit append requires an exact prepared event")
    if (
        record.idempotency_key != prepared.idempotency_key
        or record.installation_id != prepared.installation_id
        or record.repository_id != prepared.repository_id
        or record.subject_type != prepared.subject_type
        or record.subject_id != prepared.subject_id
        or record.event_type != prepared.event_type
        or record.created_at != prepared.created_at
        or record.actor != prepared.actor
        or record.payload_hash != prepared.payload_hash
        or record.input_hash != prepared.input_hash
    ):
        raise PersistenceInvariantViolation("prepared audit event does not match its record")


def _optional_hash_bytes(value: str | None) -> bytes | None:
    return None if value is None else bytes.fromhex(value)


def _bounded_bytes_field(row: Mapping[str, object], key: str, maximum: int) -> bytes:
    value = _bytes_field(row, key)
    if not 1 <= len(value) <= maximum:
        raise ValueError(f"{key} is outside its byte interval")
    return value


def _optional_positive_integer_field(row: Mapping[str, object], key: str) -> int | None:
    value = row[key]
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise TypeError(f"{key} is not a positive integer")
    return value


def _hash_hex(row: Mapping[str, object], key: str) -> str:
    value = _bytes_field(row, key)
    if len(value) != 32:
        raise ValueError(f"{key} is not a SHA-256 digest")
    return value.hex()


def _optional_hash_hex(row: Mapping[str, object], key: str) -> str | None:
    return None if row[key] is None else _hash_hex(row, key)
