from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from typing import Any, cast

import pytest

from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventRecord,
    build_audit_event,
    verify_audit_chain,
)
from ci_coordinator.audit_replay.chain import audit_chain_verification_to_mapping
from ci_coordinator.audit_replay.event import (
    audit_event_from_mapping,
    audit_event_input_from_mapping,
    audit_event_to_mapping,
)

from ._event_test_support import (
    MAX_SAFE_JSON_INTEGER,
    audit_replay_oracle,
    scalar_event_input,
)


class UnboundedKeyMapping(Mapping[str, object]):
    def __init__(self) -> None:
        self.next_calls = 0
        self.value_reads = 0

    def __iter__(self) -> Iterator[str]:
        index = 0
        while True:
            self.next_calls += 1
            yield f"key-{index}"
            index += 1

    def __len__(self) -> int:
        raise AssertionError("mapping length must not be read")

    def __getitem__(self, _key: str) -> object:
        self.value_reads += 1
        raise AssertionError("mapping values must not be read")


@pytest.mark.parametrize("input_mapping", (False, True), ids=("record", "input"))
def test_mapping_shape_caps_key_iteration_before_value_reads(input_mapping: bool) -> None:
    mapping = UnboundedKeyMapping()

    with pytest.raises(AuditEventError, match="contains unknown key key-0"):
        if input_mapping:
            audit_event_input_from_mapping(mapping)
        else:
            audit_event_from_mapping(mapping)

    assert mapping.next_calls == 17
    assert mapping.value_reads == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.0, 0),
        (MAX_SAFE_JSON_INTEGER, MAX_SAFE_JSON_INTEGER),
        (-MAX_SAFE_JSON_INTEGER, -MAX_SAFE_JSON_INTEGER),
        (1.5, 1.5),
        (0.0000001, 0.0000001),
        (5e-324, 5e-324),
        ("\U0001f600", "\U0001f600"),
    ],
)
def test_build_audit_event_normalizes_admitted_scalar_payloads(
    value: object,
    expected: object,
) -> None:
    record = build_audit_event(scalar_event_input({"value": cast(Any, value)}), None)

    assert record.payload == {"value": expected}
    assert verify_audit_chain([record]).valid is True


def test_build_audit_event_preserves_proto_as_hashed_json_data() -> None:
    record = build_audit_event(scalar_event_input({"__proto__": "evidence"}), None)

    assert record.payload == {"__proto__": "evidence"}
    assert verify_audit_chain([record]).valid is True


def test_audit_event_input_mapping_requires_exact_input_or_record_shape() -> None:
    record = audit_replay_oracle()["validChain"]["events"][0]
    input_mapping = {
        key: record[key]
        for key in (
            "idempotencyKey",
            "subjectType",
            "subjectId",
            "eventType",
            "createdAt",
            "actor",
            "payload",
        )
    }

    assert audit_event_input_from_mapping(record).idempotency_key == record["idempotencyKey"]
    with pytest.raises(AuditEventError, match="contains unknown key unauthenticated"):
        audit_event_input_from_mapping({**input_mapping, "unauthenticated": True})
    with pytest.raises(AuditEventError, match="invalid Unicode scalar values"):
        audit_event_input_from_mapping({**input_mapping, "actor": "invalid-\ud800"})


@pytest.mark.parametrize("scope_key", ("installationId", "repositoryId"))
@pytest.mark.parametrize("scope_value", (1, None, "invalid"))
def test_half_scoped_input_has_a_typed_shape_failure_before_any_value_read(
    scope_key: str, scope_value: object
) -> None:
    raw = _literal_input()
    raw[scope_key] = scope_value
    mapping = _ReadCountingMapping(raw)

    with pytest.raises(AuditEventError) as failure:
        audit_event_input_from_mapping(mapping)

    assert str(failure.value) == "audit event input has an incomplete repository scope"
    assert failure.value.audit_event_id is None
    assert mapping.reads == []
    assert mapping.iterations == len(raw)


@pytest.mark.parametrize(
    ("removed", "added", "message"),
    (
        ("actor", {"installationId": 1}, "audit event input is missing actor"),
        ("actor", {"repositoryId": 2}, "audit event input is missing actor"),
        ("actor", {}, "audit event input is missing actor"),
        (None, {"unexpected": True}, "audit event input contains unknown key unexpected"),
        (
            None,
            {"schemaVersion": "ci-audit-event/v1"},
            "audit event input is missing installationId",
        ),
    ),
)
def test_input_shape_repair_preserves_existing_diagnostic_precedence(
    removed: str | None, added: dict[str, object], message: str
) -> None:
    raw = _literal_input()
    if removed is not None:
        del raw[removed]
    raw.update(added)
    mapping = _ReadCountingMapping(raw)

    with pytest.raises(AuditEventError) as failure:
        audit_event_input_from_mapping(mapping)

    assert str(failure.value) == message
    assert failure.value.audit_event_id is None
    assert mapping.reads == []


@pytest.mark.parametrize("scoped", (False, True))
def test_valid_input_and_record_shapes_preserve_snapshot_values_and_hashes(scoped: bool) -> None:
    raw = _literal_input()
    if scoped:
        raw.update(installationId=1, repositoryId=2)
    mapping = _ReadCountingMapping(dict(reversed(tuple(raw.items()))))
    admitted = audit_event_input_from_mapping(mapping)
    assert len(mapping.reads) == len(raw)
    assert set(mapping.reads) == set(raw)
    assert mapping.reads == (
        [
            "idempotencyKey",
            "installationId",
            "repositoryId",
            "subjectType",
            "subjectId",
            "eventType",
            "createdAt",
            "actor",
            "payload",
        ]
        if scoped
        else list(_literal_input())
    )
    assert admitted.installation_id == (1 if scoped else None)
    assert admitted.repository_id == (2 if scoped else None)
    assert admitted.payload == {"value": [1, "snapshot"]}
    record = build_audit_event(admitted, None)
    record_mapping = audit_event_to_mapping(record)
    read_record = _ReadCountingMapping(dict(reversed(tuple(record_mapping.items()))))

    assert audit_event_from_mapping(read_record) == record
    assert len(read_record.reads) == len(record_mapping)
    assert set(read_record.reads) == set(record_mapping)
    assert read_record.reads[0] == "auditEventId"
    projected = audit_event_input_from_mapping(record_mapping)
    assert projected == admitted
    assert build_audit_event(projected, None) == record
    assert verify_audit_chain([record_mapping]).valid is True
    assert record_mapping == audit_event_to_mapping(audit_event_from_mapping(record_mapping))


def test_record_shape_failure_keeps_its_existing_missing_key_and_diagnostic_id() -> None:
    record = audit_replay_oracle()["validChain"]["events"][0]
    raw = {key: value for key, value in record.items() if key != "actor"}
    mapping = _ReadCountingMapping(raw)

    with pytest.raises(AuditEventError) as failure:
        audit_event_from_mapping(mapping)

    assert str(failure.value) == "audit event record is missing installationId"
    assert failure.value.audit_event_id == record["auditEventId"]
    assert mapping.reads == ["auditEventId"]
    assert audit_event_to_mapping(audit_event_from_mapping(record)) == record


def _literal_input() -> dict[str, object]:
    return {
        "idempotencyKey": "scope-totality",
        "subjectType": "dynamic-ci-plan",
        "subjectId": "scope-test",
        "eventType": "scope.checked",
        "createdAt": "2026-09-26T00:00:00.000Z",
        "actor": "test",
        "payload": {"value": [1, "snapshot"]},
    }


class _ReadCountingMapping(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self._data = values
        self.reads: list[str] = []
        self.iterations = 0

    def __iter__(self) -> Iterator[str]:
        for key in self._data:
            self.iterations += 1
            yield key

    def __len__(self) -> int:
        raise AssertionError("shape admission must not query caller-owned length")

    def __getitem__(self, key: str) -> object:
        self.reads.append(key)
        return self._data[key]


def test_verify_audit_chain_rejects_schema_and_unknown_field_drift() -> None:
    [record, *_] = audit_replay_oracle()["validChain"]["events"]

    assert audit_chain_verification_to_mapping(
        verify_audit_chain([{**record, "schemaVersion": "ci-audit-event/v2"}])
    ) == {
        "valid": False,
        "reason": "unsupported audit event schema version",
        "auditEventId": record["auditEventId"],
    }
    assert audit_chain_verification_to_mapping(
        verify_audit_chain([{**record, "unauthenticated": True}])
    ) == {
        "valid": False,
        "reason": "audit event record contains unknown key unauthenticated",
        "auditEventId": record["auditEventId"],
    }


def test_verify_audit_chain_validates_direct_record_instances() -> None:
    record = build_audit_event(
        audit_event_input_from_mapping(audit_replay_oracle()["validChain"]["events"][0]),
        None,
    )
    cases: list[tuple[AuditEventRecord, str]] = [
        (
            replace(record, schema_version=cast(Any, "ci-audit-event/v2")),
            "unsupported audit event schema version",
        ),
        (
            replace(record, subject_type=cast(Any, "unknown-subject")),
            "subject type is unsupported",
        ),
        (replace(record, created_at="not-a-date"), "createdAt must be an ISO timestamp"),
        (replace(record, actor=""), "actor must be a non-empty string"),
        (
            replace(record, idempotency_key=""),
            "idempotency key must be a non-empty string",
        ),
        (
            replace(record, sequence=cast(Any, "1")),
            "sequence must be a positive safe integer",
        ),
        (
            replace(record, sequence=cast(Any, 1.5)),
            "sequence must be a positive safe integer",
        ),
        (
            replace(record, sequence=cast(Any, 0)),
            "sequence must be a positive safe integer",
        ),
        (
            replace(record, sequence=cast(Any, -1)),
            "sequence must be a positive safe integer",
        ),
        (
            replace(record, sequence=cast(Any, MAX_SAFE_JSON_INTEGER + 1)),
            "sequence must be a positive safe integer",
        ),
    ]

    for invalid_record, reason in cases:
        assert audit_chain_verification_to_mapping(verify_audit_chain([invalid_record])) == {
            "valid": False,
            "reason": reason,
            "auditEventId": invalid_record.audit_event_id,
        }


def test_verify_audit_chain_normalizes_integral_float_sequence() -> None:
    record = audit_replay_oracle()["validChain"]["events"][0]
    normalized = audit_event_from_mapping({**record, "sequence": 1.0})

    assert audit_chain_verification_to_mapping(
        verify_audit_chain([{**record, "sequence": 1.0}])
    ) == {
        "valid": True,
        "lastEventHash": record["eventHash"],
    }
    assert type(normalized.sequence) is int
    assert normalized.sequence == 1


def test_failed_record_diagnostic_uses_the_mapping_snapshot() -> None:
    record = build_audit_event(scalar_event_input({}), None)
    raw = {**audit_event_to_mapping(record), "unauthenticated": True}
    flipping = FlippingAuditEventIdRecord(raw, record.audit_event_id)

    result = verify_audit_chain([flipping])

    assert result.valid is False
    assert result.audit_event_id == record.audit_event_id
    assert flipping.audit_event_id_reads == 1


def test_mapping_snapshot_rejects_duplicate_keys_before_value_reads() -> None:
    record = build_audit_event(scalar_event_input({}), None)
    raw_record = DuplicatePayloadMapping(audit_event_to_mapping(record))
    raw_input = DuplicatePayloadMapping(
        {
            "idempotencyKey": "audit-duplicate-input-key",
            "subjectType": "dynamic-ci-plan",
            "subjectId": "scalar-domain",
            "eventType": "scalar-domain.checked",
            "createdAt": "2026-07-09T00:00:00.000Z",
            "actor": "ci-coordinator",
            "payload": {},
        }
    )

    verification = verify_audit_chain([raw_record])
    with pytest.raises(AuditEventError, match="contains duplicate key payload"):
        audit_event_input_from_mapping(raw_input)

    assert verification.valid is False
    assert verification.reason == "audit event record contains duplicate key payload"
    assert raw_record.payload_reads == 0
    assert raw_input.payload_reads == 0


class FlippingAuditEventIdRecord(Mapping[str, object]):
    def __init__(self, record: Mapping[str, object], first_audit_event_id: str) -> None:
        self.record = record
        self.first_audit_event_id = first_audit_event_id
        self.audit_event_id_reads = 0

    def __getitem__(self, key: str) -> object:
        if key != "auditEventId":
            return self.record[key]
        self.audit_event_id_reads += 1
        if self.audit_event_id_reads == 1:
            return self.first_audit_event_id
        return f"audit_{'f' * 32}"

    def __iter__(self) -> Iterator[str]:
        return iter(self.record)

    def __len__(self) -> int:
        return len(self.record)


class DuplicatePayloadMapping(Mapping[str, object]):
    def __init__(self, record: Mapping[str, object]) -> None:
        self.record = record
        self.payload_reads = 0

    def __getitem__(self, key: str) -> object:
        if key == "payload":
            self.payload_reads += 1
        return self.record[key]

    def __iter__(self) -> Iterator[str]:
        return iter((*self.record, "payload"))

    def __len__(self) -> int:
        return len(self.record) + 1
