from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any

import pytest

from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventInput,
    build_audit_event,
    list_subject_events,
)
from ci_coordinator.audit_replay.event import (
    AuditJsonResourceFailure,
    audit_event_to_mapping,
)
from ci_coordinator.audit_replay.json_resources import AUDIT_JSON_MAX_NODES
from ci_coordinator.audit_replay.replay import (
    AllAuditReplayFilter,
    AuditEventIdReplayFilter,
    SubjectAuditReplayFilter,
    audit_replay_report_to_mapping,
    replay_audit_evidence,
)

from ._event_test_support import audit_replay_oracle


def test_replay_audit_evidence_matches_requirement_owned_vector() -> None:
    oracle = audit_replay_oracle()["validChain"]

    report = replay_audit_evidence(oracle["events"], AllAuditReplayFilter())

    assert audit_replay_report_to_mapping(report) == oracle["replay"]


def test_invalid_replay_resource_failure_mapping_is_all_fields_or_absent() -> None:
    record = build_audit_event(
        AuditEventInput(
            idempotency_key="resource-replay",
            subject_type="dynamic-ci-plan",
            subject_id="resource-replay",
            event_type="verified",
            created_at="2026-07-09T00:00:00.000Z",
            actor="ci-coordinator",
            payload={},
        ),
        None,
    )
    candidate = {
        **audit_event_to_mapping(record),
        "payload": [None] * AUDIT_JSON_MAX_NODES,
    }

    report = replay_audit_evidence(
        [candidate],
        AllAuditReplayFilter(),
    )
    ordinary_invalid = replay_audit_evidence(
        [{**audit_event_to_mapping(record), "payload": {"unexpected": True}}],
        AllAuditReplayFilter(),
    )

    resource_ledger: Any = audit_replay_report_to_mapping(report)["ledger"]
    ordinary_ledger: Any = audit_replay_report_to_mapping(ordinary_invalid)["ledger"]
    assert resource_ledger == {
        "valid": False,
        "totalEvents": 1,
        "reason": f"payload exceeds maximum JSON node count of {AUDIT_JSON_MAX_NODES}",
        "auditEventId": record.audit_event_id,
        "resourceFailure": {
            "code": "json_max_nodes_exceeded",
            "instancePointer": "/payload",
            "limit": AUDIT_JSON_MAX_NODES,
            "observed": AUDIT_JSON_MAX_NODES + 1,
        },
    }
    assert "resourceFailure" not in ordinary_ledger


def test_replay_subject_filter_matches_requirement_owned_multi_event_vector() -> None:
    oracle = audit_replay_oracle()["subjectFilter"]

    report = replay_audit_evidence(
        oracle["events"],
        SubjectAuditReplayFilter(subject_type="dynamic-ci-plan", subject_id="plan-a"),
    )

    assert audit_replay_report_to_mapping(report) == oracle["replay"]


def test_replay_audit_event_filter_matches_requirement_owned_multi_event_vector() -> None:
    oracle = audit_replay_oracle()["auditEventFilter"]
    target_event_id = oracle["replay"]["filter"]["auditEventId"]

    report = replay_audit_evidence(
        oracle["events"],
        AuditEventIdReplayFilter(audit_event_id=target_event_id),
    )

    assert audit_replay_report_to_mapping(report) == oracle["replay"]


def test_replay_missing_audit_event_matches_requirement_owned_vector() -> None:
    oracle = audit_replay_oracle()["missingAuditEvent"]
    missing_event_id = oracle["replay"]["filter"]["auditEventId"]

    report = replay_audit_evidence(
        oracle["events"],
        AuditEventIdReplayFilter(audit_event_id=missing_event_id),
    )

    assert audit_replay_report_to_mapping(report) == oracle["replay"]


def test_replay_includes_payload_only_when_requested() -> None:
    [event, *_] = audit_replay_oracle()["validChain"]["events"]
    compact = replay_audit_evidence(
        [event],
        AllAuditReplayFilter(),
    )
    with_payload = replay_audit_evidence(
        [event],
        AuditEventIdReplayFilter(audit_event_id=event["auditEventId"]),
        include_payload=True,
    )
    compact_mapping: Any = audit_replay_report_to_mapping(compact)
    with_payload_mapping: Any = audit_replay_report_to_mapping(with_payload)

    assert "payload" not in compact_mapping["events"][0]
    assert with_payload_mapping["events"][0]["payload"] == event["payload"]


def test_replay_includes_json_null_payload_when_requested() -> None:
    event = build_audit_event(
        AuditEventInput(
            idempotency_key="dynamic-ci-plan:plan-null:null-payload",
            subject_type="dynamic-ci-plan",
            subject_id="plan-null",
            event_type="dynamic-ci-plan.verified",
            created_at="2026-06-06T00:00:00.000Z",
            actor="ci-coordinator",
            payload=None,
        ),
        None,
    )

    report = replay_audit_evidence(
        [event],
        AllAuditReplayFilter(),
        include_payload=True,
    )

    mapped: Any = audit_replay_report_to_mapping(report)
    assert "payload" in mapped["events"][0]
    assert mapped["events"][0]["payload"] is None


def test_replay_verifies_whole_ledger_before_subject_filtering() -> None:
    first, second = audit_replay_oracle()["validChain"]["events"]
    corrupted_unrelated = {
        **second,
        "subjectId": "unrelated-plan",
        "payload": {"status": "corrupted", "sequence": 2},
    }

    report = replay_audit_evidence(
        [first, corrupted_unrelated],
        SubjectAuditReplayFilter(subject_type="dynamic-ci-plan", subject_id="plan-a"),
    )

    assert audit_replay_report_to_mapping(report) == {
        "ok": False,
        "status": "invalid-ledger",
        "ledger": {
            "valid": False,
            "totalEvents": 2,
            "reason": "payload hash does not match payload",
            "auditEventId": second["auditEventId"],
        },
        "filter": {
            "kind": "subject",
            "subjectType": "dynamic-ci-plan",
            "subjectId": "plan-a",
        },
        "events": [],
    }


@pytest.mark.parametrize(
    "replay_filter",
    [
        SubjectAuditReplayFilter(subject_type="dynamic-ci-plan", subject_id="missing-plan"),
        AuditEventIdReplayFilter(audit_event_id=f"audit_{'0' * 32}"),
    ],
)
def test_replay_verifies_corrupt_ledger_before_reporting_missing_target(
    replay_filter: SubjectAuditReplayFilter | AuditEventIdReplayFilter,
) -> None:
    first, second = audit_replay_oracle()["validChain"]["events"]
    corrupted = {**second, "payload": {"status": "corrupted", "sequence": 2}}

    report = replay_audit_evidence(
        [first, corrupted],
        replay_filter,
    )

    assert report.ok is False
    assert report.status == "invalid-ledger"
    assert report.ledger.reason == "payload hash does not match payload"


def test_replay_uses_the_verified_snapshot_for_payload_output() -> None:
    [event, *_] = audit_replay_oracle()["validChain"]["events"]
    flipping_event = FlippingPayloadRecord(
        event,
        first_payload=event["payload"],
        later_payload={"status": "corrupted", "sequence": 1},
    )

    report = replay_audit_evidence(
        [flipping_event],
        AllAuditReplayFilter(),
        include_payload=True,
    )
    mapped: Any = audit_replay_report_to_mapping(report)

    assert mapped["ok"] is True
    assert mapped["events"][0]["payload"] == event["payload"]


def test_list_subject_events_uses_the_verified_snapshot() -> None:
    [event, *_] = audit_replay_oracle()["validChain"]["events"]
    flipping_event = FlippingPayloadRecord(
        event,
        first_payload=event["payload"],
        later_payload={"status": "corrupted", "sequence": 1},
    )

    events = list_subject_events([flipping_event], "dynamic-ci-plan", "plan-a")

    assert len(events) == 1
    assert events[0].payload == event["payload"]


def test_list_subject_events_detaches_direct_record_payload() -> None:
    nested = {"fallback": False}
    payload: dict[str, Any] = {"planId": "plan-a", "nested": nested}
    built = build_audit_event(
        AuditEventInput(
            idempotency_key="audit-direct-record-snapshot",
            subject_type="dynamic-ci-plan",
            subject_id="plan-a",
            event_type="verified",
            created_at="2026-07-09T00:00:00.000Z",
            actor="ci-coordinator",
            payload=payload,
        ),
        None,
    )
    direct_record = replace(built, payload=payload)

    events = list_subject_events([direct_record], "dynamic-ci-plan", "plan-a")
    nested["fallback"] = True

    assert events[0].payload == {"planId": "plan-a", "nested": {"fallback": False}}


def test_list_subject_events_fails_closed_on_invalid_ledger() -> None:
    first, second = audit_replay_oracle()["validChain"]["events"]

    with pytest.raises(AuditEventError, match="invalid audit ledger"):
        list_subject_events(
            [
                first,
                {
                    **second,
                    "payload": {"status": "corrupted", "sequence": 2},
                },
            ],
            "dynamic-ci-plan",
            "plan-a",
        )


def test_list_subject_events_preserves_invalid_ledger_resource_failure() -> None:
    record = build_audit_event(
        AuditEventInput(
            idempotency_key="resource-list",
            subject_type="dynamic-ci-plan",
            subject_id="resource-list",
            event_type="verified",
            created_at="2026-07-09T00:00:00.000Z",
            actor="ci-coordinator",
            payload={},
        ),
        None,
    )
    candidate = {**audit_event_to_mapping(record), "payload": [None] * AUDIT_JSON_MAX_NODES}

    with pytest.raises(AuditEventError) as failure:
        list_subject_events(
            [candidate],
            "dynamic-ci-plan",
            "resource-list",
        )

    assert str(failure.value) == (
        f"invalid audit ledger at {record.audit_event_id}: "
        f"payload exceeds maximum JSON node count of {AUDIT_JSON_MAX_NODES}"
    )
    assert failure.value.audit_event_id == record.audit_event_id
    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="json_max_nodes_exceeded",
        instance_pointer="/payload",
        limit=AUDIT_JSON_MAX_NODES,
        observed=AUDIT_JSON_MAX_NODES + 1,
    )


def test_replay_reports_not_found_only_after_valid_ledger() -> None:
    events = audit_replay_oracle()["validChain"]["events"]

    report = replay_audit_evidence(
        events,
        SubjectAuditReplayFilter(subject_type="dynamic-ci-plan", subject_id="missing-plan"),
    )

    assert audit_replay_report_to_mapping(report) == {
        "ok": False,
        "status": "not-found",
        "ledger": {
            "valid": True,
            "totalEvents": 2,
            "lastEventHash": events[1]["eventHash"],
        },
        "filter": {
            "kind": "subject",
            "subjectType": "dynamic-ci-plan",
            "subjectId": "missing-plan",
        },
        "events": [],
    }


@dataclass
class FlippingPayloadRecord(Mapping[str, object]):
    record: Mapping[str, object]
    first_payload: object
    later_payload: object
    payload_reads: int = 0

    def __getitem__(self, key: str) -> object:
        if key != "payload":
            return self.record[key]
        self.payload_reads += 1
        if self.payload_reads == 1:
            return self.first_payload
        return self.later_payload

    def __iter__(self) -> Iterator[str]:
        return iter(self.record)

    def __len__(self) -> int:
        return len(self.record)
