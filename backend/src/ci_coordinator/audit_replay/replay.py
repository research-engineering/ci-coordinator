from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from ci_coordinator.audit_replay.chain import (
    InvalidAuditChain,
    _normalize_and_verify_audit_chain,
    invalid_audit_chain_to_event_error,
)
from ci_coordinator.audit_replay.event import (
    AuditEventRecord,
    AuditEventRecordLike,
    AuditJsonResourceFailure,
    JsonValue,
    snapshot_json_value,
)
from ci_coordinator.audit_replay.subjects import AuditSubjectType


@dataclass(frozen=True)
class AllAuditReplayFilter:
    kind: Literal["all"] = "all"


@dataclass(frozen=True)
class SubjectAuditReplayFilter:
    subject_type: AuditSubjectType
    subject_id: str
    kind: Literal["subject"] = "subject"


@dataclass(frozen=True)
class AuditEventIdReplayFilter:
    audit_event_id: str
    kind: Literal["audit-event"] = "audit-event"


type AuditReplayFilter = AllAuditReplayFilter | SubjectAuditReplayFilter | AuditEventIdReplayFilter


@dataclass(frozen=True, eq=False)
class AuditReplayLedgerSummary:
    valid: bool
    total_events: int
    last_event_hash: str | None = None
    reason: str | None = None
    audit_event_id: str | None = None
    resource_failure: AuditJsonResourceFailure | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AuditReplayLedgerSummary):
            return NotImplemented
        return (
            self.valid,
            self.total_events,
            self.last_event_hash,
            self.reason,
            self.audit_event_id,
            self.resource_failure,
        ) == (
            other.valid,
            other.total_events,
            other.last_event_hash,
            other.reason,
            other.audit_event_id,
            other.resource_failure,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.valid,
                self.total_events,
                self.last_event_hash,
                self.reason,
                self.audit_event_id,
                self.resource_failure,
            )
        )


@dataclass(frozen=True, eq=False)
class ValidAuditReplayLedgerSummary(AuditReplayLedgerSummary):
    total_events: int
    last_event_hash: str | None
    valid: Literal[True] = field(default=True, init=False)
    reason: None = field(default=None, init=False)
    audit_event_id: None = field(default=None, init=False)
    resource_failure: None = field(default=None, init=False)


@dataclass(frozen=True, eq=False)
class InvalidAuditReplayLedgerSummary(AuditReplayLedgerSummary):
    total_events: int
    reason: str
    audit_event_id: str | None
    resource_failure: AuditJsonResourceFailure | None = None
    valid: Literal[False] = field(default=False, init=False)
    last_event_hash: None = field(default=None, init=False)


@dataclass(frozen=True)
class AuditReplayEventSummary:
    audit_event_id: str
    sequence: int
    subject_type: AuditSubjectType
    subject_id: str
    event_type: str
    created_at: str
    actor: str
    payload_hash: str
    input_hash: str
    previous_event_hash: str | None
    event_hash: str
    payload: JsonValue | None = None
    payload_included: bool = False


@dataclass(frozen=True, eq=False)
class AuditReplayReport:
    ok: bool
    status: Literal["valid", "invalid-ledger", "not-found"]
    ledger: AuditReplayLedgerSummary
    replay_filter: AuditReplayFilter
    events: tuple[AuditReplayEventSummary, ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AuditReplayReport):
            return NotImplemented
        return (
            self.ok,
            self.status,
            self.ledger,
            self.replay_filter,
            self.events,
        ) == (
            other.ok,
            other.status,
            other.ledger,
            other.replay_filter,
            other.events,
        )

    def __hash__(self) -> int:
        return hash((self.ok, self.status, self.ledger, self.replay_filter, self.events))


@dataclass(frozen=True, eq=False)
class ValidAuditReplayReport(AuditReplayReport):
    ledger: ValidAuditReplayLedgerSummary
    replay_filter: AuditReplayFilter
    events: tuple[AuditReplayEventSummary, ...]
    ok: Literal[True] = field(default=True, init=False)
    status: Literal["valid"] = field(default="valid", init=False)


@dataclass(frozen=True, eq=False)
class InvalidLedgerAuditReplayReport(AuditReplayReport):
    ledger: InvalidAuditReplayLedgerSummary
    replay_filter: AuditReplayFilter
    events: tuple[()] = field(default=(), init=False)
    ok: Literal[False] = field(default=False, init=False)
    status: Literal["invalid-ledger"] = field(default="invalid-ledger", init=False)


@dataclass(frozen=True, eq=False)
class NotFoundAuditReplayReport(AuditReplayReport):
    ledger: ValidAuditReplayLedgerSummary
    replay_filter: AuditReplayFilter
    events: tuple[()] = field(default=(), init=False)
    ok: Literal[False] = field(default=False, init=False)
    status: Literal["not-found"] = field(default="not-found", init=False)


type AuditReplayResult = (
    ValidAuditReplayReport | InvalidLedgerAuditReplayReport | NotFoundAuditReplayReport
)


def replay_audit_evidence(
    records: Sequence[AuditEventRecordLike],
    replay_filter: AuditReplayFilter,
    *,
    include_payload: bool = False,
) -> AuditReplayResult:
    normalized_events, verification = _normalize_and_verify_audit_chain(records)
    if isinstance(verification, InvalidAuditChain):
        return InvalidLedgerAuditReplayReport(
            ledger=InvalidAuditReplayLedgerSummary(
                total_events=len(records),
                reason=verification.reason,
                audit_event_id=verification.audit_event_id,
                resource_failure=verification.resource_failure,
            ),
            replay_filter=replay_filter,
        )

    ledger = ValidAuditReplayLedgerSummary(
        total_events=len(normalized_events),
        last_event_hash=verification.last_event_hash,
    )
    events = tuple(
        summarize_audit_event(event, include_payload=include_payload)
        for event in filter_audit_events(normalized_events, replay_filter)
    )
    if not isinstance(replay_filter, AllAuditReplayFilter) and len(events) == 0:
        return NotFoundAuditReplayReport(
            ledger=ledger,
            replay_filter=replay_filter,
        )

    return ValidAuditReplayReport(
        ledger=ledger,
        replay_filter=replay_filter,
        events=events,
    )


def replay_subject(
    records: Sequence[AuditEventRecordLike],
    subject_type: AuditSubjectType,
    subject_id: str,
    *,
    include_payload: bool = False,
) -> AuditReplayResult:
    return replay_audit_evidence(
        records,
        SubjectAuditReplayFilter(subject_type=subject_type, subject_id=subject_id),
        include_payload=include_payload,
    )


def list_subject_events(
    records: Sequence[AuditEventRecordLike],
    subject_type: AuditSubjectType,
    subject_id: str,
) -> tuple[AuditEventRecord, ...]:
    normalized_events, verification = _normalize_and_verify_audit_chain(records)
    if isinstance(verification, InvalidAuditChain):
        raise invalid_audit_chain_to_event_error(verification)
    return tuple(
        event
        for event in normalized_events
        if event.subject_type == subject_type and event.subject_id == subject_id
    )


def audit_replay_report_to_mapping(report: AuditReplayReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "status": report.status,
        "ledger": audit_replay_ledger_to_mapping(report.ledger),
        "filter": audit_replay_filter_to_mapping(report.replay_filter),
        "events": [audit_replay_event_summary_to_mapping(event) for event in report.events],
    }


def filter_audit_events(
    events: Sequence[AuditEventRecord],
    replay_filter: AuditReplayFilter,
) -> tuple[AuditEventRecord, ...]:
    return tuple(event for event in events if audit_event_matches_filter(event, replay_filter))


def audit_event_matches_filter(
    event: AuditEventRecord,
    replay_filter: AuditReplayFilter,
) -> bool:
    if isinstance(replay_filter, AllAuditReplayFilter):
        return True
    if isinstance(replay_filter, SubjectAuditReplayFilter):
        return (
            event.subject_type == replay_filter.subject_type
            and event.subject_id == replay_filter.subject_id
        )
    return event.audit_event_id == replay_filter.audit_event_id


def summarize_audit_event(
    event: AuditEventRecord,
    *,
    include_payload: bool,
) -> AuditReplayEventSummary:
    return AuditReplayEventSummary(
        audit_event_id=event.audit_event_id,
        sequence=event.sequence,
        subject_type=event.subject_type,
        subject_id=event.subject_id,
        event_type=event.event_type,
        created_at=event.created_at,
        actor=event.actor,
        payload_hash=event.payload_hash,
        input_hash=event.input_hash,
        previous_event_hash=event.previous_event_hash,
        event_hash=event.event_hash,
        payload=snapshot_json_value(event.payload) if include_payload else None,
        payload_included=include_payload,
    )


def audit_replay_ledger_to_mapping(summary: AuditReplayLedgerSummary) -> dict[str, object]:
    if summary.valid:
        return {
            "valid": True,
            "totalEvents": summary.total_events,
            "lastEventHash": summary.last_event_hash,
        }
    mapped: dict[str, object] = {
        "valid": False,
        "totalEvents": summary.total_events,
        "reason": summary.reason,
        "auditEventId": summary.audit_event_id,
    }
    if summary.resource_failure is not None:
        mapped["resourceFailure"] = summary.resource_failure.to_mapping()
    return mapped


def audit_replay_filter_to_mapping(replay_filter: AuditReplayFilter) -> dict[str, object]:
    if isinstance(replay_filter, AllAuditReplayFilter):
        return {"kind": "all"}
    if isinstance(replay_filter, SubjectAuditReplayFilter):
        return {
            "kind": "subject",
            "subjectType": replay_filter.subject_type,
            "subjectId": replay_filter.subject_id,
        }
    return {"kind": "audit-event", "auditEventId": replay_filter.audit_event_id}


def audit_replay_event_summary_to_mapping(event: AuditReplayEventSummary) -> dict[str, object]:
    result: dict[str, object] = {
        "auditEventId": event.audit_event_id,
        "sequence": event.sequence,
        "subjectType": event.subject_type,
        "subjectId": event.subject_id,
        "eventType": event.event_type,
        "createdAt": event.created_at,
        "actor": event.actor,
        "payloadHash": event.payload_hash,
        "inputHash": event.input_hash,
        "previousEventHash": event.previous_event_hash,
        "eventHash": event.event_hash,
    }
    if event.payload_included:
        result["payload"] = snapshot_json_value(event.payload)
    return result
