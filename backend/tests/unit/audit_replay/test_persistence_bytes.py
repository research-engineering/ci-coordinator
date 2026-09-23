from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import pytest

import ci_coordinator.audit_replay.persistence_bytes as persistence_bytes_module
from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventInput,
    AuditJsonResourceFailure,
    PreparedAuditEvent,
    build_prepared_audit_event,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.persistence_bytes import (
    AUDIT_PERSISTENCE_BYTE_FIELD_PROJECTIONS_V1,
    AUDIT_PERSISTENCE_BYTE_PROFILE_V1,
    AUDIT_TEXT_BYTE_FIELD_PROJECTIONS_V1,
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
)
from ci_coordinator.kernel import CanonicalJsonError, bounded_canonical_json, sha256_hex

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_python_projection_matches_the_normative_audit_byte_profile() -> None:
    profile = json.loads(
        (
            REPO_ROOT / "docs/specs/ci-coordinator-core/audit-persistence-byte-profile.v1.json"
        ).read_text(encoding="utf8")
    )

    assert AUDIT_PERSISTENCE_BYTE_PROFILE_V1.schema_version == profile["schemaVersion"]
    assert AUDIT_PERSISTENCE_BYTE_PROFILE_V1.profile_id == profile["profileId"]
    assert profile["limits"] == {
        "maxVariableTextUtf8Bytes": MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        "maxPayloadCanonicalBytes": MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    }
    assert [
        {
            "instancePointer": field.instance_pointer,
            "storageColumn": field.storage_column,
            "measureRef": field.measure_ref,
            "limitRef": field.limit_ref,
            "nonEmpty": field.non_empty,
        }
        for field in AUDIT_PERSISTENCE_BYTE_FIELD_PROJECTIONS_V1
    ] == profile["fieldProjections"]


def test_generic_bounded_canonical_json_has_exact_saturated_failure() -> None:
    assert bounded_canonical_json({"a": "b"}, max_bytes=9) == b'{"a":"b"}'

    with pytest.raises(CanonicalJsonError) as failure:
        bounded_canonical_json({"a": "b"}, max_bytes=8)

    assert failure.value.code == "canonical_json_max_bytes_exceeded"
    assert failure.value.instance_pointer == ""
    assert failure.value.limit == 8
    assert failure.value.observed == 9


@pytest.mark.parametrize(
    "field",
    ["idempotency_key", "subject_id", "event_type", "actor"],
)
def test_audit_text_limit_measures_utf8_bytes(
    field: Literal["idempotency_key", "subject_id", "event_type", "actor"],
) -> None:
    base = event_input()
    exact = "\U0001f600" * (MAX_AUDIT_TEXT_UTF8_BYTES_V1 // 4)
    admitted = replace_text_field(base, field, exact)

    assert getattr(prepare_audit_event(admitted), field) == exact

    rejected = replace_text_field(base, field, exact + "a")
    with pytest.raises(AuditEventError) as failure:
        prepare_audit_event(rejected)

    projection = next(
        item for item in AUDIT_TEXT_BYTE_FIELD_PROJECTIONS_V1 if item.attribute == field
    )
    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="audit_text_max_utf8_bytes_exceeded",
        instance_pointer=projection.instance_pointer,
        limit=MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        observed=MAX_AUDIT_TEXT_UTF8_BYTES_V1 + 1,
    )


def test_payload_limit_measures_exact_canonical_escaped_bytes() -> None:
    exact_payload = "a" * (MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 - 2)
    prepared = prepare_audit_event(event_input(payload=exact_payload))

    assert len(prepared.payload_canonical_bytes) == MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1

    with pytest.raises(AuditEventError) as failure:
        prepare_audit_event(event_input(payload=exact_payload + "a"))

    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="audit_payload_max_canonical_bytes_exceeded",
        instance_pointer="/payload",
        limit=MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
        observed=MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 + 1,
    )


def test_payload_scalar_failure_precedes_deferred_byte_failure() -> None:
    payload = {
        "oversized": "a" * (MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1 + 1),
        "unsafe": 9_007_199_254_740_992,
    }

    with pytest.raises(
        AuditEventError,
        match=r"payload\.unsafe must be a JSON safe integer",
    ) as failure:
        prepare_audit_event(event_input(payload=cast(Any, payload)))

    assert failure.value.resource_failure is None


def test_metadata_byte_failure_precedes_payload_admission_and_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_calls = 0
    hash_calls = 0

    def observed_canonical(*args: object, **kwargs: object) -> bytes:
        nonlocal canonical_calls
        canonical_calls += 1
        return b"{}"

    def observed_hash(_value: bytes) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return "0" * 64

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(persistence_bytes_module, "bounded_canonical_json", observed_canonical)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(persistence_bytes_module, "sha256_hex", observed_hash)
    rejected = replace(
        event_input(payload=cast(Any, object())),
        actor="a" * (MAX_AUDIT_TEXT_UTF8_BYTES_V1 + 1),
    )

    with pytest.raises(AuditEventError) as failure:
        prepare_audit_event(rejected)

    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="audit_text_max_utf8_bytes_exceeded",
        instance_pointer="/actor",
        limit=MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        observed=MAX_AUDIT_TEXT_UTF8_BYTES_V1 + 1,
    )
    assert canonical_calls == 0
    assert hash_calls == 0


def test_prepared_event_retains_exact_bytes_and_predecessor_independent_hashes() -> None:
    prepared = prepare_audit_event(event_input(payload={"z": 1, "a": 'quoted"'}))
    record = build_prepared_audit_event(prepared, None)

    assert prepared.payload_canonical_bytes == b'{"a":"quoted\\"","z":1}'
    assert prepared.payload_hash == sha256_hex(prepared.payload_canonical_bytes)
    assert record.payload_hash == prepared.payload_hash
    assert record.input_hash == prepared.input_hash


def test_prepared_event_detaches_caller_payload_and_never_recanonicalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = {"accepted": True}
    payload = {"nested": nested}
    prepared = prepare_audit_event(event_input(payload=payload))
    nested["accepted"] = False

    def poisoned_canonical(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        raise AssertionError("prepared build must not canonicalize")

    def poisoned_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("prepared build must not parse")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(persistence_bytes_module, "bounded_canonical_json", poisoned_canonical)
    monkeypatch.setattr(json, "loads", poisoned_parse)
    record = build_prepared_audit_event(prepared, None)

    assert record.payload == {"nested": {"accepted": True}}
    assert prepared.payload_canonical_bytes == b'{"nested":{"accepted":true}}'


def test_prepared_event_transfers_payload_ownership_exactly_once() -> None:
    prepared = prepare_audit_event(event_input(payload={"value": 1}))
    first = build_prepared_audit_event(prepared, None)
    cast(dict[str, object], first.payload)["value"] = 2

    with pytest.raises(AuditEventError, match="already been consumed"):
        build_prepared_audit_event(prepared, None)

    assert prepared.payload_canonical_bytes == b'{"value":1}'
    assert prepared.payload_hash == sha256_hex(prepared.payload_canonical_bytes)


def test_prepared_event_rejects_direct_construction_subclass_copy_and_mutation() -> None:
    constructor: Any = PreparedAuditEvent
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        constructor(
            object(),
            idempotency_key="key",
            subject_type="dynamic-ci-plan",
            subject_id="subject",
            event_type="event",
            created_at="2026-07-11T00:00:00.000Z",
            actor="actor",
            payload_snapshot={},
            payload_canonical_bytes=b"{}",
            payload_hash="0" * 64,
            input_hash="0" * 64,
        )

    with pytest.raises(TypeError, match="cannot be subclassed"):

        class PreparedSubclass(PreparedAuditEvent):
            pass

    prepared = prepare_audit_event(event_input())
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(prepared)
    with pytest.raises(TypeError, match="immutable"):
        prepared.actor = "changed"  # type: ignore[misc]


def test_prepared_event_rejects_structural_forgery() -> None:
    forged = cast(Any, PreparedLookalike())

    with pytest.raises(AuditEventError, match="exact PreparedAuditEvent"):
        build_prepared_audit_event(forged, None)


def event_input(*, payload: Any | None = None) -> AuditEventInput:
    return AuditEventInput(
        idempotency_key="dynamic-ci-plan:plan-1:persistence-bytes",
        subject_type="dynamic-ci-plan",
        subject_id="plan-1",
        event_type="dynamic-ci-plan.planned",
        created_at="2026-07-11T00:00:00.000Z",
        actor="ci-coordinator",
        payload={} if payload is None else payload,
    )


def replace_text_field(
    source: AuditEventInput,
    field: Literal["idempotency_key", "subject_id", "event_type", "actor"],
    value: str,
) -> AuditEventInput:
    if field == "idempotency_key":
        return replace(source, idempotency_key=value)
    if field == "subject_id":
        return replace(source, subject_id=value)
    if field == "event_type":
        return replace(source, event_type=value)
    return replace(source, actor=value)


class PreparedLookalike:
    idempotency_key = "forged"
    subject_type = "dynamic-ci-plan"
    subject_id = "plan-1"
    event_type = "dynamic-ci-plan.planned"
    created_at = "2026-07-11T00:00:00.000Z"
    actor = "ci-coordinator"
    payload_canonical_bytes = b"{}"
    payload_hash = "0" * 64
    input_hash = "0" * 64
