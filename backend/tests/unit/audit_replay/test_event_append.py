from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from typing import Any

import pytest

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendConflict,
    AuditAppendDuplicate,
    AuditAppendResult,
    AuditEventError,
    AuditEventInput,
    AuditEventRecord,
    append_event,
    build_audit_event,
)
from ci_coordinator.audit_replay.event import (
    AuditJsonResourceFailure,
    audit_event_input_from_mapping,
    audit_event_to_mapping,
)
from ci_coordinator.audit_replay.json_resources import AUDIT_JSON_MAX_NODES

from ._event_test_support import (
    audit_replay_oracle,
    scalar_event_input,
)


def test_append_event_classifies_local_duplicate_and_conflict_semantics() -> None:
    event_input = AuditEventInput(
        idempotency_key="dynamic-ci-plan:plan-1:verified",
        subject_type="dynamic-ci-plan",
        subject_id="plan-1",
        event_type="dynamic-ci-plan.verified",
        created_at="2026-06-06T00:00:00.000Z",
        actor="ci-coordinator",
        payload={"planId": "plan-1", "inputHash": "input-a"},
    )
    first = append_event([], event_input)
    assert isinstance(first, AuditAppendAppended)
    assert first.kind == "appended"
    assert first.record.payload == event_input.payload

    duplicate = append_event(
        [record_from_append(first)],
        AuditEventInput(
            idempotency_key=event_input.idempotency_key,
            subject_type=event_input.subject_type,
            subject_id=event_input.subject_id,
            event_type=event_input.event_type,
            created_at="2026-06-06T00:00:01.000Z",
            actor="ci-coordinator-retry",
            payload=event_input.payload,
        ),
    )
    conflict = append_event(
        [record_from_append(first)],
        AuditEventInput(
            idempotency_key=event_input.idempotency_key,
            subject_type=event_input.subject_type,
            subject_id=event_input.subject_id,
            event_type=event_input.event_type,
            created_at="2026-06-06T00:00:02.000Z",
            actor=event_input.actor,
            payload={"planId": "plan-1", "inputHash": "input-b"},
        ),
    )

    assert isinstance(duplicate, AuditAppendDuplicate)
    assert duplicate.record == record_from_append(first)
    assert isinstance(conflict, AuditAppendConflict)
    assert conflict.existing == record_from_append(first)
    assert conflict.attempted.payload == {"planId": "plan-1", "inputHash": "input-b"}


def test_append_event_fails_closed_on_invalid_existing_ledger() -> None:
    [record, *_] = audit_replay_oracle()["validChain"]["events"]

    with pytest.raises(AuditEventError, match="invalid audit ledger"):
        append_event(
            [{**record, "payload": {"status": "corrupted", "sequence": 1}}],
            audit_event_input_from_mapping(record),
        )


def test_append_event_preserves_invalid_ledger_resource_failure() -> None:
    record = build_audit_event(scalar_event_input({}), None)
    candidate = {**audit_event_to_mapping(record), "payload": [None] * AUDIT_JSON_MAX_NODES}

    with pytest.raises(AuditEventError) as failure:
        append_event(
            [candidate],
            scalar_event_input({}, idempotency_key="resource-append-attempt"),
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


def test_append_event_reuses_the_single_verified_ledger_snapshot() -> None:
    event_input = AuditEventInput(
        idempotency_key="dynamic-ci-plan:plan-1:single-snapshot",
        subject_type="dynamic-ci-plan",
        subject_id="plan-1",
        event_type="dynamic-ci-plan.verified",
        created_at="2026-06-06T00:00:00.000Z",
        actor="ci-coordinator",
        payload={"planId": "plan-1", "fallback": False},
    )
    existing = build_audit_event(event_input, None)
    flipping = FlippingPayloadRecord(
        audit_event_to_mapping(existing),
        first_payload=existing.payload,
        later_payload={"planId": "plan-1", "fallback": True},
    )

    result = append_event([flipping], event_input)

    assert isinstance(result, AuditAppendDuplicate)
    assert result.record.payload == existing.payload
    assert flipping.payload_reads == 1


def test_append_event_snapshots_attempt_before_reading_the_ledger() -> None:
    attempted_payload: dict[str, Any] = {"value": "attempted"}
    attempted_input = AuditEventInput(
        idempotency_key="dynamic-ci-plan:plan-1:input-before-ledger",
        subject_type="dynamic-ci-plan",
        subject_id="plan-1",
        event_type="dynamic-ci-plan.verified",
        created_at="2026-06-06T00:00:00.000Z",
        actor="ci-coordinator",
        payload=attempted_payload,
    )
    existing = build_audit_event(
        replace(attempted_input, payload={"value": "original"}),
        None,
    )
    mutating_ledger = InputMutatingRecord(
        audit_event_to_mapping(existing),
        mutate=lambda: attempted_payload.update(value="original"),
    )

    result = append_event([mutating_ledger], attempted_input)

    assert isinstance(result, AuditAppendConflict)
    assert result.attempted.payload == {"value": "attempted"}


def record_from_append(result: AuditAppendResult) -> AuditEventRecord:
    if isinstance(result, AuditAppendAppended | AuditAppendDuplicate):
        return result.record
    raise AssertionError("expected append result with record")


class FlippingPayloadRecord(Mapping[str, object]):
    def __init__(
        self,
        record: Mapping[str, object],
        *,
        first_payload: object,
        later_payload: object,
    ) -> None:
        self.record = record
        self.first_payload = first_payload
        self.later_payload = later_payload
        self.payload_reads = 0

    def __getitem__(self, key: str) -> object:
        if key != "payload":
            return self.record[key]
        self.payload_reads += 1
        return self.first_payload if self.payload_reads == 1 else self.later_payload

    def __iter__(self) -> Iterator[str]:
        return iter(self.record)

    def __len__(self) -> int:
        return len(self.record)


class InputMutatingRecord(Mapping[str, object]):
    def __init__(self, record: Mapping[str, object], *, mutate: Callable[[], None]) -> None:
        self.record = record
        self.mutate = mutate

    def __getitem__(self, key: str) -> object:
        return self.record[key]

    def __iter__(self) -> Iterator[str]:
        self.mutate()
        return iter(self.record)

    def __len__(self) -> int:
        return len(self.record)
