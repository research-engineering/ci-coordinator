from __future__ import annotations

from ci_coordinator.audit_replay._event_contracts import (
    AUDIT_EVENT_SCHEMA_VERSION,
    AuditEventInput,
    AuditEventRecord,
)
from ci_coordinator.audit_replay._event_validation import _record_input
from ci_coordinator.audit_replay.subjects import AuditSubjectType
from ci_coordinator.kernel import hash_object


def audit_input_hash(event_input: AuditEventInput, payload_hash: str) -> str:
    identity: dict[str, object] = {
        "schemaVersion": AUDIT_EVENT_SCHEMA_VERSION,
        "idempotencyKey": event_input.idempotency_key,
        "subjectType": event_input.subject_type,
        "subjectId": event_input.subject_id,
        "eventType": event_input.event_type,
        "payloadHash": payload_hash,
    }
    _add_scope(identity, event_input.installation_id, event_input.repository_id)
    return hash_object(identity)


def audit_input_hash_for_record(record: AuditEventRecord, payload_hash: str) -> str:
    return audit_input_hash(_record_input(record), payload_hash)


def audit_event_hash(
    *,
    schema_version: str,
    idempotency_key: str,
    installation_id: int | None = None,
    repository_id: int | None = None,
    subject_type: AuditSubjectType,
    subject_id: str,
    event_type: str,
    created_at: str,
    actor: str,
    sequence: int,
    previous_event_hash: str | None,
    payload_hash: str,
    input_hash: str,
) -> str:
    identity: dict[str, object] = {
        "schemaVersion": schema_version,
        "idempotencyKey": idempotency_key,
        "subjectType": subject_type,
        "subjectId": subject_id,
        "eventType": event_type,
        "createdAt": created_at,
        "actor": actor,
        "sequence": sequence,
        "previousEventHash": previous_event_hash,
        "payloadHash": payload_hash,
        "inputHash": input_hash,
    }
    _add_scope(identity, installation_id, repository_id)
    return hash_object(identity)


def _add_scope(
    identity: dict[str, object],
    installation_id: int | None,
    repository_id: int | None,
) -> None:
    if installation_id is not None:
        identity["installationId"] = installation_id
        identity["repositoryId"] = repository_id
