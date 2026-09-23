from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ci_coordinator.audit_replay import (
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
    AuditEventInput,
    build_prepared_audit_event,
    prepare_audit_event,
)
from ci_coordinator.persistence.audit_codec import prepared_record_to_row, row_to_record
from ci_coordinator.persistence.errors import PersistenceInvariantViolation


@pytest.mark.parametrize(
    ("column", "maximum"),
    (
        ("idempotency_key", MAX_AUDIT_TEXT_UTF8_BYTES_V1),
        ("subject_id", MAX_AUDIT_TEXT_UTF8_BYTES_V1),
        ("event_type", MAX_AUDIT_TEXT_UTF8_BYTES_V1),
        ("actor", MAX_AUDIT_TEXT_UTF8_BYTES_V1),
        ("payload_canonical_json", MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1),
    ),
)
def test_raw_byte_overflow_rejects_before_json_parse(
    monkeypatch: pytest.MonkeyPatch,
    column: str,
    maximum: int,
) -> None:
    row = _valid_row()
    row[column] = b"x" * (maximum + 1)
    parser_calls = 0

    def forbidden_parser(_value: object) -> object:
        nonlocal parser_calls
        parser_calls += 1
        raise AssertionError("JSON parser must not run before raw byte admission")

    monkeypatch.setattr(json, "loads", forbidden_parser)

    with pytest.raises(PersistenceInvariantViolation, match="stored audit event is invalid"):
        row_to_record(row)

    assert parser_calls == 0


@pytest.mark.parametrize(
    "payload_bytes",
    (
        b"\xff",
        b"{",
        b'{"z":1,"a":2}',
    ),
)
def test_length_valid_invalid_payload_bytes_are_not_promoted_to_records(
    payload_bytes: bytes,
) -> None:
    row = _valid_row()
    row["payload_canonical_json"] = payload_bytes

    with pytest.raises(PersistenceInvariantViolation, match="stored audit event is invalid"):
        row_to_record(row)


def test_length_valid_node_exhaustion_is_not_promoted_to_a_record() -> None:
    row = _valid_row()
    row["payload_canonical_json"] = b"[" + b",".join([b"null"] * 10_001) + b"]"

    with pytest.raises(PersistenceInvariantViolation, match="stored audit event is invalid"):
        row_to_record(row)


@pytest.mark.parametrize(
    "field", ("idempotency_key", "subject_id", "event_type", "actor", "payload")
)
def test_v1_reader_preserves_exact_retained_boundary(field: str) -> None:
    maximum = (
        MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 - 2
        if field == "payload"
        else MAX_AUDIT_TEXT_UTF8_BYTES_V1
    )
    value = "x" * maximum
    event = _valid_input()
    match field:
        case "idempotency_key":
            event = replace(event, idempotency_key=value)
        case "subject_id":
            event = replace(event, subject_id=value)
        case "event_type":
            event = replace(event, event_type=value)
        case "actor":
            event = replace(event, actor=value)
        case "payload":
            event = replace(event, payload=value)
        case _:
            raise AssertionError("unknown audit field")
    prepared = prepare_audit_event(event)
    record = build_prepared_audit_event(prepared, None)

    assert row_to_record(prepared_record_to_row(record, prepared)) == record


def _valid_input() -> AuditEventInput:
    return AuditEventInput(
        idempotency_key="codec-byte-test",
        subject_type="dynamic-ci-plan",
        subject_id="plan-codec-byte-test",
        event_type="dynamic-ci-plan.persisted",
        created_at="2026-07-11T00:00:00.000Z",
        actor="test-suite",
        payload={"accepted": True},
    )


def _valid_row() -> dict[str, object]:
    prepared = prepare_audit_event(_valid_input())
    return prepared_record_to_row(build_prepared_audit_event(prepared, None), prepared)
