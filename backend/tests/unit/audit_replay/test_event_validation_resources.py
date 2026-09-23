from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from typing import Any, Literal, cast

import pytest

import ci_coordinator.audit_replay._event_identity as event_identity_module
import ci_coordinator.audit_replay.persistence_bytes as persistence_bytes_module
from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventInput,
    build_audit_event,
    verify_audit_chain,
)
from ci_coordinator.audit_replay.chain import (
    InvalidAuditChain,
    audit_chain_verification_to_mapping,
)
from ci_coordinator.audit_replay.event import (
    AuditJsonResourceFailure,
    audit_event_to_mapping,
)
from ci_coordinator.audit_replay.json_resources import (
    AUDIT_JSON_MAX_DEPTH,
    AUDIT_JSON_MAX_NODES,
    AUDIT_JSON_RESOURCE_LIMITS_V1,
)
from ci_coordinator.kernel import JsonResourceLimits

from ._event_test_support import (
    MAX_SAFE_JSON_INTEGER,
    REPO_ROOT,
    scalar_event_input,
)


def test_audit_limits_project_the_normative_resource_profile() -> None:
    profile = json.loads(
        (
            REPO_ROOT / "docs/specs/ci-coordinator-core/audit-json-resource-profile.v1.json"
        ).read_text(encoding="utf8")
    )

    assert profile["profileId"] == "ci-audit-event-json-resources/v1"
    assert profile["limits"] == {
        "maxDepth": AUDIT_JSON_MAX_DEPTH,
        "maxNodes": AUDIT_JSON_MAX_NODES,
    }
    assert (
        JsonResourceLimits(
            max_depth=profile["limits"]["maxDepth"],
            max_nodes=profile["limits"]["maxNodes"],
        )
        == AUDIT_JSON_RESOURCE_LIMITS_V1
    )


class AuditEventInputSubclass(AuditEventInput):
    pass


def test_resource_exhaustion_is_typed_and_precedes_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hash_calls = 0

    def observed_hash(_value: object) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return "0" * 64

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(event_identity_module, "hash_object", observed_hash)

    with pytest.raises(AuditEventError) as failure:
        build_audit_event(
            scalar_event_input(cast(Any, [None] * AUDIT_JSON_MAX_NODES)),
            None,
        )

    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="json_max_nodes_exceeded",
        instance_pointer="/payload",
        limit=AUDIT_JSON_MAX_NODES,
        observed=AUDIT_JSON_MAX_NODES + 1,
    )
    assert hash_calls == 0


@pytest.mark.parametrize(
    ("key", "expected_pointer"),
    [
        ("a.b", "/payload/a.b/0"),
        ("a/b", "/payload/a~1b/0"),
        ("m~n", "/payload/m~0n/0"),
        ("", "/payload//0"),
    ],
)
def test_resource_failure_uses_prefixed_rfc_6901_instance_pointer(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    expected_pointer: str,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        persistence_bytes_module,
        "AUDIT_JSON_RESOURCE_LIMITS_V1",
        JsonResourceLimits(max_depth=1, max_nodes=10),
    )

    with pytest.raises(AuditEventError) as failure:
        build_audit_event(scalar_event_input({key: [None]}), None)

    assert str(failure.value) == f"payload.{key}[0] exceeds maximum JSON depth of 1"
    assert failure.value.resource_failure == AuditJsonResourceFailure(
        code="json_max_depth_exceeded",
        instance_pointer=expected_pointer,
        limit=1,
        observed=2,
    )


def test_scalar_error_message_preserves_free_form_human_path() -> None:
    with pytest.raises(AuditEventError) as failure:
        build_audit_event(
            scalar_event_input({"a/b": MAX_SAFE_JSON_INTEGER + 1}),
            None,
        )

    assert str(failure.value) == "payload.a/b must be a JSON safe integer"
    assert failure.value.resource_failure is None


def test_resource_failure_state_is_atomic_and_frozen() -> None:
    failure_type: Any = AuditJsonResourceFailure
    with pytest.raises(TypeError):
        failure_type(
            code="json_max_nodes_exceeded",
            instance_pointer="/payload/9999",
            limit=AUDIT_JSON_MAX_NODES,
        )

    failure = AuditJsonResourceFailure(
        code="json_max_nodes_exceeded",
        instance_pointer="/payload/9999",
        limit=AUDIT_JSON_MAX_NODES,
        observed=AUDIT_JSON_MAX_NODES + 1,
    )
    mutable_failure: Any = failure
    with pytest.raises(FrozenInstanceError):
        mutable_failure.observed = AUDIT_JSON_MAX_NODES + 2

    event_error_type: Any = AuditEventError
    with pytest.raises(TypeError):
        event_error_type(
            "resource failure",
            code="json_max_nodes_exceeded",
            limit=AUDIT_JSON_MAX_NODES,
        )

    invalid_chain_type: Any = InvalidAuditChain
    with pytest.raises(TypeError):
        invalid_chain_type(
            reason="resource failure",
            audit_event_id=None,
            resource_code="json_max_nodes_exceeded",
        )


def test_chain_verification_preserves_resource_failure_facts() -> None:
    record = build_audit_event(scalar_event_input({}), None)
    candidate = {**audit_event_to_mapping(record), "payload": [None] * AUDIT_JSON_MAX_NODES}

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


def test_structural_budget_resets_for_each_audit_record() -> None:
    payload = cast(Any, [None] * (AUDIT_JSON_MAX_NODES - 1))
    first = build_audit_event(
        scalar_event_input(payload, idempotency_key="audit-resource-reset-1"),
        None,
    )
    second = build_audit_event(
        scalar_event_input(payload, idempotency_key="audit-resource-reset-2"),
        first,
    )

    assert verify_audit_chain([first, second]).valid is True


def test_build_audit_event_rejects_input_subclasses_before_field_reads() -> None:
    base = scalar_event_input({})
    subclass = AuditEventInputSubclass(
        idempotency_key=base.idempotency_key,
        subject_type=base.subject_type,
        subject_id=base.subject_id,
        event_type=base.event_type,
        created_at=base.created_at,
        actor=base.actor,
        payload=base.payload,
    )

    with pytest.raises(AuditEventError, match="must be an exact AuditEventInput"):
        build_audit_event(subclass, None)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"value": MAX_SAFE_JSON_INTEGER + 1}, "payload.value must be a JSON safe integer"),
        (
            {"value": -(MAX_SAFE_JSON_INTEGER + 1)},
            "payload.value must be a JSON safe integer",
        ),
        (
            {"value": float(MAX_SAFE_JSON_INTEGER + 1)},
            "payload.value must be a JSON safe integer",
        ),
        ({"value": 1e20}, "payload.value must be a JSON safe integer"),
        ({"value": 1e21}, "payload.value must be a JSON safe integer"),
        (
            {"value": "\ud800"},
            "payload.value must not contain invalid Unicode scalar values",
        ),
        (
            {"\ud800": "value"},
            "payload must not contain invalid Unicode scalar values",
        ),
    ],
)
def test_build_audit_event_rejects_out_of_domain_scalar_payloads(
    payload: dict[str, Any],
    reason: str,
) -> None:
    with pytest.raises(AuditEventError, match=reason):
        build_audit_event(scalar_event_input(payload), None)


@pytest.mark.parametrize(
    "field",
    ["idempotency_key", "subject_id", "event_type", "actor"],
)
@pytest.mark.parametrize("surrogate", ["\ud800", "\udc00"])
def test_build_audit_event_rejects_unpaired_surrogate_audit_text(
    field: Literal["idempotency_key", "subject_id", "event_type", "actor"],
    surrogate: str,
) -> None:
    base = scalar_event_input({})
    if field == "idempotency_key":
        event_input = replace(base, idempotency_key=f"invalid-{surrogate}")
    elif field == "subject_id":
        event_input = replace(base, subject_id=f"invalid-{surrogate}")
    elif field == "event_type":
        event_input = replace(base, event_type=f"invalid-{surrogate}")
    else:
        event_input = replace(base, actor=f"invalid-{surrogate}")

    with pytest.raises(AuditEventError, match="invalid Unicode scalar values"):
        build_audit_event(event_input, None)


@pytest.mark.parametrize(
    "created_at",
    [
        "0001-01-01T00:00:00.000Z",
        "2000-02-29T23:59:59.999Z",
        "9999-12-31T23:59:59.999Z",
    ],
)
def test_build_audit_event_accepts_canonical_timestamp_domain(created_at: str) -> None:
    record = build_audit_event(scalar_event_input({}, created_at=created_at), None)

    assert record.created_at == created_at


@pytest.mark.parametrize(
    "created_at",
    [
        "0000-01-01T00:00:00.000Z",
        "+010000-01-01T00:00:00.000Z",
        "-000001-01-01T00:00:00.000Z",
        "1900-02-29T00:00:00.000Z",
        "2026-07-09T00:00:00Z",
        "2026-07-09T00:00:00.000+00:00",
    ],
)
def test_build_audit_event_rejects_noncanonical_timestamps(created_at: str) -> None:
    with pytest.raises(AuditEventError, match="createdAt must be an ISO timestamp"):
        build_audit_event(scalar_event_input({}, created_at=created_at), None)


def test_build_audit_event_rejects_sequence_overflow_before_hashing() -> None:
    first = build_audit_event(scalar_event_input({}), None)
    previous = replace(first, sequence=MAX_SAFE_JSON_INTEGER)

    with pytest.raises(AuditEventError, match="sequence must be a positive safe integer"):
        build_audit_event(
            scalar_event_input({}, idempotency_key="audit-scalar-sequence-overflow"),
            previous,
        )
