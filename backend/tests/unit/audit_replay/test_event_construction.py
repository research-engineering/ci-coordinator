from __future__ import annotations

from typing import Any

import ci_coordinator.audit_replay as audit_replay
from ci_coordinator.audit_replay import (
    AuditEventInput,
    build_audit_event,
    verify_audit_chain,
)
from ci_coordinator.audit_replay.chain import audit_chain_verification_to_mapping
from ci_coordinator.audit_replay.event import (
    audit_event_input_from_mapping,
    audit_event_to_mapping,
)

from ._event_test_support import audit_replay_oracle


def test_package_facade_excludes_unadmitted_verifier_stages() -> None:
    assert {
        "normalize_and_verify_audit_chain",
        "verify_normalized_audit_chain",
    }.isdisjoint(audit_replay.__all__)


def test_build_audit_event_matches_requirement_owned_audit_replay_vector() -> None:
    oracle = audit_replay_oracle()["validChain"]
    first = build_audit_event(audit_event_input_from_mapping(oracle["events"][0]), None)
    second = build_audit_event(audit_event_input_from_mapping(oracle["events"][1]), first)

    assert audit_event_to_mapping(first) == oracle["events"][0]
    assert audit_event_to_mapping(second) == oracle["events"][1]
    assert (
        audit_chain_verification_to_mapping(verify_audit_chain([first, second]))
        == oracle["verification"]
    )


def test_build_audit_event_stores_payload_snapshot() -> None:
    nested = {"fallback": False}
    payload: dict[str, Any] = {"planId": "plan-1", "nested": nested}
    record = build_audit_event(
        AuditEventInput(
            idempotency_key="dynamic-ci-plan:plan-1:canonical-copy",
            subject_type="dynamic-ci-plan",
            subject_id="plan-1",
            event_type="dynamic-ci-plan.verified",
            created_at="2026-06-06T00:00:00.000Z",
            actor="ci-coordinator",
            payload=payload,
        ),
        None,
    )

    nested["fallback"] = True

    assert record.payload == {"planId": "plan-1", "nested": {"fallback": False}}
    assert verify_audit_chain([record]).valid is True
