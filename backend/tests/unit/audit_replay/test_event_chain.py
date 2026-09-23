from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

import ci_coordinator.audit_replay._event_identity as event_identity_module
from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventInput,
    build_audit_event,
    verify_audit_chain,
    verify_audit_chain_extension,
)
from ci_coordinator.audit_replay.chain import audit_chain_verification_to_mapping
from ci_coordinator.audit_replay.event import (
    AuditJsonResourceFailure,
    audit_event_hash,
    audit_event_input_from_mapping,
    audit_event_to_mapping,
)
from ci_coordinator.audit_replay.json_resources import AUDIT_JSON_MAX_NODES
from ci_coordinator.kernel import hash_object

from ._event_test_support import (
    audit_replay_oracle,
    scalar_event_input,
)


def test_verify_audit_chain_matches_requirement_owned_corruption_vector() -> None:
    corrupted = audit_replay_oracle()["corruptedPayload"]
    events = audit_replay_oracle()["validChain"]["events"]

    assert (
        audit_chain_verification_to_mapping(
            verify_audit_chain(
                [
                    events[0],
                    {
                        **events[1],
                        "payload": {"status": "corrupted", "sequence": 2},
                    },
                ]
            )
        )
        == corrupted["verification"]
    )


def test_verify_audit_chain_matches_requirement_owned_first_failure_precedence() -> None:
    oracle = audit_replay_oracle()
    first, second = oracle["validChain"]["events"]

    assert (
        audit_chain_verification_to_mapping(
            verify_audit_chain(
                [
                    first,
                    {**second, "payload": {"status": "corrupted", "sequence": 2}},
                    {**second, "schemaVersion": "ci-audit-event/v2"},
                ]
            )
        )
        == oracle["dualFaultPrecedence"]["verification"]
    )


def test_verify_audit_chain_extension_binds_a_verified_prefix() -> None:
    first = build_audit_event(
        scalar_event_input({}, idempotency_key="extension-first"),
        None,
    )
    second = build_audit_event(
        scalar_event_input({"value": 2}, idempotency_key="extension-second"),
        first,
    )

    unchanged = verify_audit_chain_extension(first, ())
    valid = verify_audit_chain_extension(first, (second,))
    invalid = verify_audit_chain_extension(
        first,
        (replace(second, previous_event_hash="0" * 64),),
    )

    assert unchanged.valid is True
    assert unchanged.last_event_hash == first.event_hash
    assert valid.valid is True
    assert valid.last_event_hash == second.event_hash
    assert invalid.valid is False
    assert invalid.reason == "previous event hash does not match"


def test_current_preparation_precedes_previous_record_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = build_audit_event(scalar_event_input({}), None)
    oversized_previous = {
        **audit_event_to_mapping(previous),
        "payload": [None] * AUDIT_JSON_MAX_NODES,
    }
    hash_calls = 0

    def observed_hash(_value: object) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return "0" * 64

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(event_identity_module, "hash_object", observed_hash)

    with pytest.raises(AuditEventError) as failure:
        build_audit_event(
            scalar_event_input({}, idempotency_key="resource-current"),
            oversized_previous,
        )

    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="json_max_nodes_exceeded",
        instance_pointer="/payload",
        limit=AUDIT_JSON_MAX_NODES,
        observed=AUDIT_JSON_MAX_NODES + 1,
    )
    assert hash_calls == 1


def test_record_resource_failure_precedes_invalid_metadata() -> None:
    record = build_audit_event(scalar_event_input({}), None)
    candidate = {
        **audit_event_to_mapping(record),
        "idempotencyKey": "",
        "payload": [None] * AUDIT_JSON_MAX_NODES,
    }

    assert audit_chain_verification_to_mapping(verify_audit_chain([candidate])) == {
        "valid": False,
        "reason": f"payload exceeds maximum JSON node count of {AUDIT_JSON_MAX_NODES}",
        "auditEventId": record.audit_event_id,
        "resourceFailure": {
            "code": "json_max_nodes_exceeded",
            "instancePointer": "/payload",
            "limit": AUDIT_JSON_MAX_NODES,
            "observed": AUDIT_JSON_MAX_NODES + 1,
        },
    }


def test_verify_audit_chain_rejects_self_consistent_boolean_sequence() -> None:
    record = build_audit_event(
        audit_event_input_from_mapping(audit_replay_oracle()["validChain"]["events"][0]),
        None,
    )
    bool_sequence_hash = audit_event_hash(
        schema_version=record.schema_version,
        idempotency_key=record.idempotency_key,
        subject_type=record.subject_type,
        subject_id=record.subject_id,
        event_type=record.event_type,
        created_at=record.created_at,
        actor=record.actor,
        sequence=True,
        previous_event_hash=record.previous_event_hash,
        payload_hash=record.payload_hash,
        input_hash=record.input_hash,
    )
    invalid = replace(
        record,
        sequence=cast(Any, True),
        audit_event_id=f"audit_{bool_sequence_hash[:32]}",
        event_hash=bool_sequence_hash,
    )

    assert audit_chain_verification_to_mapping(verify_audit_chain([invalid])) == {
        "valid": False,
        "reason": "sequence must be a positive safe integer",
        "auditEventId": invalid.audit_event_id,
    }


def test_verify_audit_chain_hashes_every_input_identity_field() -> None:
    record = build_audit_event(
        audit_event_input_from_mapping(audit_replay_oracle()["validChain"]["events"][0]),
        None,
    )
    changed_payload: dict[str, Any] = {"status": "changed", "sequence": 1}
    cases = [
        replace(record, idempotency_key="audit-case-changed"),
        replace(record, subject_type="policy-drift"),
        replace(record, subject_id="plan-b"),
        replace(record, event_type="changed"),
        replace(record, payload=changed_payload, payload_hash=hash_object(changed_payload)),
    ]

    for changed in cases:
        assert audit_chain_verification_to_mapping(verify_audit_chain([changed])) == {
            "valid": False,
            "reason": "input hash does not match audit event input",
            "auditEventId": changed.audit_event_id,
        }


def test_verify_audit_chain_hashes_repository_scope_as_one_pair() -> None:
    event_input = AuditEventInput(
        idempotency_key="scoped-audit-event",
        subject_type="dynamic-ci-plan",
        subject_id="scoped-plan",
        event_type="scoped-plan.checked",
        created_at="2026-07-09T00:00:00.000Z",
        actor="ci-coordinator",
        payload={"outcome": "accepted"},
        installation_id=100,
        repository_id=200,
    )
    record = build_audit_event(event_input, None)

    assert verify_audit_chain([record]).valid
    for changed in (
        replace(record, installation_id=101),
        replace(record, repository_id=201),
    ):
        assert audit_chain_verification_to_mapping(verify_audit_chain([changed])) == {
            "valid": False,
            "reason": "input hash does not match audit event input",
            "auditEventId": changed.audit_event_id,
        }


def test_verify_audit_chain_hashes_provenance_and_validates_derived_identity() -> None:
    record = build_audit_event(
        audit_event_input_from_mapping(audit_replay_oracle()["validChain"]["events"][0]),
        None,
    )
    other_hash = "0" * 64
    cases = [
        (replace(record, actor="other-actor"), "event hash does not match event identity"),
        (
            replace(record, created_at="2026-07-09T00:00:01.000Z"),
            "event hash does not match event identity",
        ),
        (
            replace(
                record,
                event_hash=other_hash,
                audit_event_id=f"audit_{other_hash[:32]}",
            ),
            "event hash does not match event identity",
        ),
        (
            replace(record, audit_event_id=f"audit_{'0' * 32}"),
            "audit event id does not match event hash",
        ),
    ]

    for changed, reason in cases:
        assert audit_chain_verification_to_mapping(verify_audit_chain([changed])) == {
            "valid": False,
            "reason": reason,
            "auditEventId": changed.audit_event_id,
        }


def test_verify_audit_chain_rejects_deleted_reordered_and_duplicate_events() -> None:
    first, second = audit_replay_oracle()["validChain"]["events"]

    assert audit_chain_verification_to_mapping(verify_audit_chain([second])) == {
        "valid": False,
        "reason": "expected sequence 1, got 2",
        "auditEventId": second["auditEventId"],
    }
    assert audit_chain_verification_to_mapping(verify_audit_chain([second, first])) == {
        "valid": False,
        "reason": "expected sequence 1, got 2",
        "auditEventId": second["auditEventId"],
    }


def test_verify_audit_chain_reaches_self_consistent_chain_link_and_idempotency_guards() -> None:
    first = build_audit_event(
        audit_event_input_from_mapping(audit_replay_oracle()["validChain"]["events"][0]),
        None,
    )
    unrelated_genesis = build_audit_event(
        AuditEventInput(
            idempotency_key="audit-case-unrelated-genesis",
            subject_type="release-evidence",
            subject_id="release-a",
            event_type="captured",
            created_at="2026-07-09T00:00:30.000Z",
            actor="ci-coordinator",
            payload={"status": "captured"},
        ),
        None,
    )
    wrong_previous_second = build_audit_event(
        AuditEventInput(
            idempotency_key="audit-case-002",
            subject_type="dynamic-ci-plan",
            subject_id="plan-a",
            event_type="verified",
            created_at="2026-07-09T00:01:00.000Z",
            actor="ci-coordinator",
            payload={"status": "verified", "sequence": 2},
        ),
        unrelated_genesis,
    )
    duplicate_second = build_audit_event(
        audit_event_input_from_mapping(audit_replay_oracle()["validChain"]["events"][0]),
        first,
    )
    conflicting_second = build_audit_event(
        AuditEventInput(
            idempotency_key="audit-case-001",
            subject_type="dynamic-ci-plan",
            subject_id="plan-a",
            event_type="created",
            created_at="2026-07-09T00:00:00.000Z",
            actor="ci-coordinator",
            payload={"status": "conflicting", "sequence": 1},
        ),
        first,
    )

    assert (
        audit_chain_verification_to_mapping(verify_audit_chain([first, wrong_previous_second]))
        == audit_replay_oracle()["wrongPreviousHash"]["verification"]
    )
    assert (
        audit_chain_verification_to_mapping(verify_audit_chain([first, duplicate_second]))
        == audit_replay_oracle()["duplicateIdempotencyKey"]["verification"]
    )
    assert (
        audit_chain_verification_to_mapping(verify_audit_chain([first, conflicting_second]))
        == audit_replay_oracle()["conflictingIdempotencyKey"]["verification"]
    )
