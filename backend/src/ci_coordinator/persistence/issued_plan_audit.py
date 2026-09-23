"""Store-owned audit projection for an immutable issued-plan record."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from ci_coordinator.audit_replay import AuditEventInput, PreparedAuditEvent, prepare_audit_event
from ci_coordinator.kernel import hash_object
from ci_coordinator.plan_issuance import IssuedPlanRecord

ISSUED_PLAN_AUDIT_EVENT_TYPE: Final = "dynamic-ci-plan.issued"
_ISSUED_PLAN_AUDIT_SCHEMA: Final = "ci-coordinator.audit.issued-plan/v1"


def prepare_issued_plan_audit(record: IssuedPlanRecord) -> PreparedAuditEvent:
    """Derive the pair-owned event only from the record retained by the store."""

    if type(record) is not IssuedPlanRecord:
        raise TypeError("issued-plan audit requires an exact retained record")
    envelope = record.envelope
    payload = envelope.payload
    return prepare_audit_event(
        AuditEventInput(
            idempotency_key=f"issued-plan:{record.record_id}",
            installation_id=payload.repository.installation_id,
            repository_id=payload.repository.repository_id,
            subject_type="dynamic-ci-plan",
            subject_id=payload.plan_id,
            event_type=ISSUED_PLAN_AUDIT_EVENT_TYPE,
            created_at=_timestamp(envelope.issued_at),
            actor="ci-coordinator:plan-issuance",
            payload={
                "schemaVersion": _ISSUED_PLAN_AUDIT_SCHEMA,
                "recordId": record.record_id,
                "requestHash": record.request_hash,
                "envelopeHash": hash_object(
                    {
                        "unsignedEnvelope": envelope.unsigned_mapping(),
                        "signature": envelope.signature,
                    }
                ),
                "planId": payload.plan_id,
                "keyId": envelope.key_id,
                "algorithm": envelope.algorithm,
                "fallback": payload.fallback_reason is not None,
            },
        )
    )


def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("issued-plan audit timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
