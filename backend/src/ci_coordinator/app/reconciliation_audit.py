"""Redacted audit projection for terminal reconciliation state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from ci_coordinator.audit_replay import AuditEventInput
from ci_coordinator.kernel import hash_object
from ci_coordinator.reconciliation import ReconciliationResult, ReconciliationSubject

RECONCILIATION_TERMINAL_AUDIT_EVENT_TYPE: Final = "reconciliation-state.terminal"
_RECONCILIATION_AUDIT_SCHEMA: Final = "ci-coordinator.audit.reconciliation/v1"


def reconciliation_terminal_audit_event(
    subject: ReconciliationSubject,
    result: ReconciliationResult,
    *,
    revision: int,
    occurred_at: datetime | str,
) -> AuditEventInput:
    if result.subject_id != subject.subject_id:
        raise RuntimeError("reconciliation result does not bind the persisted subject")
    if result.state == "pending":
        raise ValueError("pending reconciliation state is not terminal")
    result_hash = _result_hash(result)
    return AuditEventInput(
        idempotency_key=(
            f"reconciliation-terminal:{subject.subject_id}:{revision}:{result_hash[:32]}"
        ),
        installation_id=subject.installation_id,
        repository_id=subject.repository_id,
        subject_type="reconciliation-state",
        subject_id=subject.subject_id,
        event_type=RECONCILIATION_TERMINAL_AUDIT_EVENT_TYPE,
        created_at=_audit_timestamp(occurred_at),
        actor="ci-coordinator:reconciliation",
        payload={
            "schemaVersion": _RECONCILIATION_AUDIT_SCHEMA,
            "state": result.state,
            "revision": revision,
            "resultHash": result_hash,
            "findingKinds": [finding.kind for finding in result.findings],
        },
    )


def _result_hash(result: ReconciliationResult) -> str:
    return hash_object(
        {
            "subjectId": result.subject_id,
            "state": result.state,
            "findings": [
                {
                    "kind": finding.kind,
                    "signalId": finding.signal_id,
                    "observationIds": list(finding.observation_ids),
                    "message": finding.message,
                }
                for finding in result.findings
            ],
        }
    )


def _audit_timestamp(value: datetime | str) -> str:
    if type(value) is str:
        return value
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("audit timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
