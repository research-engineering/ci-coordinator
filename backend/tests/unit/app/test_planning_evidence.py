from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ci_coordinator.app.planning_evidence import prepare_planning_evidence_audit
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.reconciliation import (
    PlanningEvidenceContext,
    ReconciliationContract,
    ReconciliationSubject,
)

SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)


def test_planning_evidence_audit_binds_the_exact_durable_contract() -> None:
    subject = _subject()
    contract = ReconciliationContract(
        (SIGNAL,),
        (),
        planning_evidence=_evidence(),
    )

    event = prepare_planning_evidence_audit(
        subject,
        contract,
        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert event.idempotency_key == f"planning-evidence:{subject.subject_id}"
    assert event.event_type == "dynamic-ci-plan.verified"
    assert event.subject_id == "verified-plan-1"
    assert json.loads(event.payload_canonical_bytes) == {
        "schemaVersion": "ci-coordinator.audit.planning-evidence/v1",
        "reconciliationSubjectId": subject.subject_id,
        "contractHash": contract.contract_hash,
        "planningEvidence": _evidence().canonical_mapping(),
    }


def test_planning_evidence_audit_rejects_a_contract_without_evidence() -> None:
    with pytest.raises(ValueError, match="requires planning evidence"):
        prepare_planning_evidence_audit(
            _subject(),
            ReconciliationContract((SIGNAL,), ()),
            occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
        )


def _subject() -> ReconciliationSubject:
    return ReconciliationSubject.create(
        installation_id=1,
        repository_id=2,
        event_name="push",
        ref="refs/heads/main",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=3,
        run_attempt=1,
    )


def _evidence() -> PlanningEvidenceContext:
    return PlanningEvidenceContext(
        request_hash="1" * 64,
        input_hash="2" * 64,
        config_epoch_id="3" * 64,
        repo_epoch_hash="4" * 64,
        diff_hash="5" * 64,
        policy_hash="6" * 64,
        graph_hash="7" * 64,
        validation_catalog_hash="8" * 64,
        deterministic_plan_id="deterministic-plan-1",
        verified_plan_id="verified-plan-1",
        verified_plan_hash="9" * 64,
        planner_version="planning-core/v1",
        verifier_version="verification-core/v1",
        fallback_reason=None,
    )
