from __future__ import annotations

from ci_coordinator.audit_replay._event_codec import audit_event_from_mapping
from ci_coordinator.audit_replay._event_contracts import (
    AUDIT_EVENT_SCHEMA_VERSION,
    AuditEventError,
    AuditEventInput,
    AuditEventRecord,
    AuditEventRecordLike,
)
from ci_coordinator.audit_replay._event_identity import audit_event_hash
from ci_coordinator.audit_replay._prepared_event import (
    PreparedAuditEvent,
    _payload_from_prepared_audit_event,
    _require_exact_prepared_audit_event,
)
from ci_coordinator.audit_replay.persistence_bytes import prepare_audit_event
from ci_coordinator.kernel import is_safe_json_integer


def build_audit_event(
    event_input: AuditEventInput,
    previous_event: AuditEventRecordLike | None,
) -> AuditEventRecord:
    prepared = prepare_audit_event(event_input)
    return build_prepared_audit_event(prepared, previous_event)


def build_prepared_audit_event(
    prepared: PreparedAuditEvent,
    previous_event: AuditEventRecordLike | None,
) -> AuditEventRecord:
    _require_exact_prepared_audit_event(prepared)
    previous = audit_event_from_mapping(previous_event) if previous_event is not None else None
    sequence = previous.sequence + 1 if previous is not None else 1
    if not is_safe_json_integer(sequence) or sequence < 1:
        raise AuditEventError("sequence must be a positive safe integer")
    previous_event_hash = previous.event_hash if previous is not None else None
    payload = _payload_from_prepared_audit_event(prepared)
    event_hash = audit_event_hash(
        schema_version=AUDIT_EVENT_SCHEMA_VERSION,
        idempotency_key=prepared.idempotency_key,
        installation_id=prepared.installation_id,
        repository_id=prepared.repository_id,
        subject_type=prepared.subject_type,
        subject_id=prepared.subject_id,
        event_type=prepared.event_type,
        created_at=prepared.created_at,
        actor=prepared.actor,
        sequence=sequence,
        previous_event_hash=previous_event_hash,
        payload_hash=prepared.payload_hash,
        input_hash=prepared.input_hash,
    )

    return AuditEventRecord(
        idempotency_key=prepared.idempotency_key,
        installation_id=prepared.installation_id,
        repository_id=prepared.repository_id,
        subject_type=prepared.subject_type,
        subject_id=prepared.subject_id,
        event_type=prepared.event_type,
        created_at=prepared.created_at,
        actor=prepared.actor,
        payload=payload,
        schema_version=AUDIT_EVENT_SCHEMA_VERSION,
        audit_event_id=f"audit_{event_hash[:32]}",
        sequence=sequence,
        previous_event_hash=previous_event_hash,
        payload_hash=prepared.payload_hash,
        input_hash=prepared.input_hash,
        event_hash=event_hash,
    )
