from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ci_coordinator.audit_replay.chain import (
    InvalidAuditChain,
    _normalize_and_verify_audit_chain,
    invalid_audit_chain_to_event_error,
)
from ci_coordinator.audit_replay.event import (
    AuditEventError,
    AuditEventInput,
    AuditEventRecord,
    AuditEventRecordLike,
    build_audit_event,
    snapshot_audit_event_input,
)


@dataclass(frozen=True)
class AuditAppendAppended:
    record: AuditEventRecord
    kind: Literal["appended"] = "appended"


@dataclass(frozen=True)
class AuditAppendDuplicate:
    record: AuditEventRecord
    kind: Literal["duplicate"] = "duplicate"


@dataclass(frozen=True)
class AuditAppendConflict:
    existing: AuditEventRecord
    attempted: AuditEventRecord
    reason: str
    kind: Literal["conflict"] = "conflict"


type AuditAppendResult = AuditAppendAppended | AuditAppendDuplicate | AuditAppendConflict


def append_event(
    existing_records: Sequence[AuditEventRecordLike],
    event_input: AuditEventInput,
) -> AuditAppendResult:
    input_snapshot = snapshot_audit_event_input(event_input)
    records, verification = _normalize_and_verify_audit_chain(existing_records)
    if isinstance(verification, InvalidAuditChain):
        raise invalid_audit_chain_to_event_error(verification)

    previous_event = records[-1] if records else None
    attempted = build_audit_event(input_snapshot, previous_event)

    for existing in records:
        if existing.idempotency_key != attempted.idempotency_key:
            continue
        if existing.input_hash == attempted.input_hash:
            return AuditAppendDuplicate(record=existing)
        return AuditAppendConflict(
            existing=existing,
            attempted=attempted,
            reason=(
                f"audit event idempotency key {attempted.idempotency_key} "
                "already exists with different content"
            ),
        )

    if any(record.audit_event_id == attempted.audit_event_id for record in records):
        raise AuditEventError(f"audit event {attempted.audit_event_id} already exists")

    return AuditAppendAppended(record=attempted)
