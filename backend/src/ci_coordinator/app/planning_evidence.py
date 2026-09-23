"""Pair-owned audit projection for durable planning evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, cast

from ci_coordinator.audit_replay import (
    AuditEventInput,
    PreparedAuditEvent,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.reconciliation import ReconciliationContract, ReconciliationSubject

PLANNING_EVIDENCE_AUDIT_EVENT_TYPE: Final = "dynamic-ci-plan.verified"
_PLANNING_EVIDENCE_AUDIT_SCHEMA: Final = "ci-coordinator.audit.planning-evidence/v1"


def prepare_planning_evidence_audit(
    subject: ReconciliationSubject,
    contract: ReconciliationContract,
    *,
    occurred_at: datetime | str,
) -> PreparedAuditEvent:
    """Derive one audit event from the exact durable subject contract."""

    if type(subject) is not ReconciliationSubject or type(contract) is not ReconciliationContract:
        raise TypeError("planning evidence audit requires an exact subject and contract")
    evidence = contract.planning_evidence
    if evidence is None:
        raise ValueError("planning evidence audit requires planning evidence")
    return prepare_audit_event(
        AuditEventInput(
            idempotency_key=f"planning-evidence:{subject.subject_id}",
            installation_id=subject.installation_id,
            repository_id=subject.repository_id,
            subject_type="dynamic-ci-plan",
            subject_id=evidence.verified_plan_id,
            event_type=PLANNING_EVIDENCE_AUDIT_EVENT_TYPE,
            created_at=_timestamp(occurred_at),
            actor="ci-coordinator:dynamic-planning",
            payload={
                "schemaVersion": _PLANNING_EVIDENCE_AUDIT_SCHEMA,
                "reconciliationSubjectId": subject.subject_id,
                "contractHash": contract.contract_hash,
                "planningEvidence": cast(JsonValue, evidence.canonical_mapping()),
            },
        )
    )


def _timestamp(value: datetime | str) -> str:
    if type(value) is str:
        return value
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("planning evidence audit timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
