from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from scripts.conformance.audit_persistence_byte_cases import (
    audit_persistence_byte_cases,
)
from scripts.conformance.audit_persistence_byte_contract import (
    validate_audit_persistence_byte_oracles,
)

import ci_coordinator.persistence.audit_codec as codec_module
from ci_coordinator.audit_replay import (
    AuditEventInput,
    build_prepared_audit_event,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.persistence_bytes import (
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
)
from ci_coordinator.persistence.audit_codec import prepared_record_to_row, row_to_record
from ci_coordinator.persistence.errors import PersistenceInvariantViolation

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_contract_corpus_fingerprint_is_admitted() -> None:
    projection = audit_persistence_byte_cases(REPOSITORY_ROOT)
    cases = projection["cases"]
    validate_audit_persistence_byte_oracles(projection, projection)

    assert projection["profileId"] == "ci-audit-event-persistence-bytes/v1"
    assert isinstance(cases, dict)
    assert projection["caseCount"] == len(cases)


@pytest.fixture
def prepared_row() -> Iterator[dict[str, object]]:
    prepared = prepare_audit_event(
        AuditEventInput(
            idempotency_key="audit-persistence-byte-contract",
            subject_type="dynamic-ci-plan",
            subject_id="byte-contract",
            event_type="byte-contract.checked",
            created_at="2026-07-11T00:00:00.000Z",
            actor="ci-coordinator",
            payload={"a": 1, "z": 2},
        )
    )
    record = build_prepared_audit_event(prepared, None)
    yield prepared_record_to_row(record, prepared)


def test_write_codec_uses_prepared_bytes_without_recanonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_audit_event(
        AuditEventInput(
            idempotency_key="audit-persistence-byte-write-contract",
            subject_type="dynamic-ci-plan",
            subject_id="byte-contract",
            event_type="byte-contract.checked",
            created_at="2026-07-11T00:00:00.000Z",
            actor="ci-coordinator",
            payload={"z": 1, "a": 'quoted"'},
        )
    )
    record = build_prepared_audit_event(prepared, None)

    def poisoned_canonicalizer(_value: object) -> bytes:
        raise AssertionError("write codec must not recanonicalize prepared payload")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(codec_module, "canonical_audit_payload_bytes", poisoned_canonicalizer)
    row = prepared_record_to_row(record, prepared)

    assert row["payload_canonical_json"] == prepared.payload_canonical_bytes


def test_read_codec_checks_raw_bound_before_decode_or_parse(
    prepared_row: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_row["payload_canonical_json"] = b"x" * (MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 + 1)

    def poisoned_parser(_value: object) -> object:
        raise AssertionError("oversized raw bytes reached the JSON parser")

    monkeypatch.setattr(json, "loads", poisoned_parser)
    with pytest.raises(PersistenceInvariantViolation):
        row_to_record(prepared_row)


@pytest.mark.parametrize(
    "invalid_payload",
    (b"\xff", b"{", b'{"z":1,"a":2}'),
    ids=("invalid-utf8", "invalid-json", "noncanonical-json"),
)
def test_read_codec_rejects_length_valid_invalid_payload_bytes(
    prepared_row: dict[str, object],
    invalid_payload: bytes,
) -> None:
    prepared_row["payload_canonical_json"] = invalid_payload

    with pytest.raises(PersistenceInvariantViolation):
        row_to_record(prepared_row)
