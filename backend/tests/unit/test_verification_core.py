from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import NoReturn

import pytest
from planning_core._support import make_input_value, make_policy_value

from ci_coordinator.agent_risk_advice import (
    AdviceEvaluationEvidence,
    AdviceExecutionEnvelope,
)
from ci_coordinator.kernel import canonical_json, hash_object
from ci_coordinator.planning_core import (
    AgentAdvicePolicy,
    DeterministicPlan,
    ImpactAnalysis,
    NotOmittable,
    OmissionProof,
    PlanningPolicy,
    PlanningRejected,
    SelectedObligation,
    SelectedWitness,
    build_omission_proof,
    close_selected_witnesses,
    full_ci_fallback_plan,
    plan,
)
from ci_coordinator.planning_core import planner as planner_module
from ci_coordinator.repo_context import DiffFileChangeInput, PlanningInput
from ci_coordinator.validation_contract import ValidationObligation
from ci_coordinator.verification_core import (
    admit_deterministic_plan,
    compare_coverage,
    validate_omission_proof,
    verify,
)
from ci_coordinator.verification_core import model as verified_model
from ci_coordinator.verification_core.model import VerificationEvidence, make_verified_plan


def test_agent_advice_can_only_strengthen_obligation_and_witness_coverage() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    candidate = _candidate(plan(input, policy))

    verified = verify(
        input,
        policy,
        candidate,
        _envelope(input.input_hash, _advice(add_obligations=["backend-tests"])),
    )

    assert [item.obligation_id for item in verified.selected_obligations] == [
        "backend-tests",
        "docs-lint",
        "required-baseline",
    ]
    shared = next(
        item for item in verified.selected_witnesses if item.witness_id == "shared-quality"
    )
    assert shared.depth == "targeted"
    assert shared.required_by_obligation_ids == ("backend-tests", "docs-lint")
    assert verified.catalog is policy.catalog
    assert verified.identity_mapping()["validationCatalog"] == policy.catalog.to_identity_mapping()
    assert verified.agent_advice is not None
    assert verified.agent_advice.accepted is True


def test_stale_deterministic_plan_forces_full_validation() -> None:
    policy = make_policy_value()
    current = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    stale = make_input_value(
        DiffFileChangeInput(path="src/service/component.py", status="modified")
    )

    verified = verify(current, policy, _candidate(plan(stale, policy)))

    assert verified.fallback.triggered is True
    assert verified.fallback.reason == "deterministic_plan_mismatch"
    assert [item.depth for item in verified.selected_obligations] == [
        "full",
        "standard",
        "full",
    ]


@pytest.mark.parametrize("sign", (b"", b"-"), ids=("positive", "negative"))
def test_huge_literal_confidence_retains_the_independently_admitted_plan(sign: bytes) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    candidate = _candidate(plan(input, policy))
    baseline = verify(input, policy, candidate)
    assert admit_deterministic_plan(input, policy, candidate) is None
    assert baseline.fallback.triggered is False
    assert baseline.omitted_obligations
    raw = (
        b'{"schemaVersion":"agent-risk-advice/v2","adviceId":"confidence-totality",'
        b'"confidence":'
        + sign
        + b"1"
        + b"0" * 309
        + b',"addObligations":[],"increaseDepth":[],"riskFindings":[],'
        b'"fallbackRecommendation":null,"rationale":"bounded test"}'
    )
    envelope = _envelope_bytes(input.input_hash, raw)

    verified = verify(input, policy, candidate, envelope)

    assert verified.source_plan == baseline.source_plan == candidate
    assert verified.selected_obligations == baseline.selected_obligations
    assert verified.selected_witnesses == baseline.selected_witnesses
    assert verified.omitted_obligations == baseline.omitted_obligations
    assert verified.deterministic_evidence == baseline.deterministic_evidence
    assert verified.fallback == baseline.fallback
    assert verified.agent_advice is not None
    assert verified.agent_advice.accepted is False
    assert verified.agent_advice.parse_valid is False
    assert verified.agent_advice.reasons == ("agent_advice_confidence_invalid",)
    assert verified.agent_advice.advice_hash == hashlib.sha256(raw).hexdigest()
    assert verified.verification_evidence == (
        *baseline.verification_evidence,
        VerificationEvidence("agent_advice_rejected", "agent_advice_confidence_invalid"),
    )

    stale = replace(candidate, input_hash="f" * 64)
    rejected = verify(input, policy, stale, envelope)
    assert rejected.fallback.triggered is True
    assert rejected.fallback.reason == "deterministic_plan_mismatch"
    assert rejected.agent_advice is None


@pytest.mark.parametrize(
    "policy",
    (
        replace(make_policy_value(), config_epoch_id="f" * 64),
        replace(make_policy_value(), compiled_policy_hash="f" * 64),
        replace(make_policy_value(), policy_hash="f" * 64),
    ),
    ids=("config-epoch", "compiled-policy", "source-policy"),
)
def test_full_ci_candidate_rejects_a_mismatched_policy_context(
    policy: PlanningPolicy,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    candidate = full_ci_fallback_plan(input, policy, "provider_unavailable")

    verified = verify(input, policy, candidate)

    assert verified.fallback.triggered is True
    assert verified.fallback.reason == "deterministic_plan_mismatch"


@pytest.mark.parametrize(
    "authority_field",
    (
        "runner_profile_id",
        "permission_profile_id",
        "credential_profile_id",
        "fixture_profile_id",
        "service_profile_ids",
        "capacity_class_id",
    ),
)
def test_agent_obligation_cannot_expand_execution_authority(
    authority_field: str,
) -> None:
    base_policy = make_policy_value()
    catalog = base_policy.catalog
    profile = catalog.execution_profiles[0]
    privileged_profile = replace(
        profile,
        profile_id="python-privileged",
        runner_profile_id="privileged-runner"
        if authority_field == "runner_profile_id"
        else profile.runner_profile_id,
        permission_profile_id="contents-write"
        if authority_field == "permission_profile_id"
        else profile.permission_profile_id,
        credential_profile_id="deploy"
        if authority_field == "credential_profile_id"
        else profile.credential_profile_id,
        fixture_profile_id="integration"
        if authority_field == "fixture_profile_id"
        else profile.fixture_profile_id,
        service_profile_ids=("database",)
        if authority_field == "service_profile_ids"
        else profile.service_profile_ids,
        capacity_class_id="privileged-capacity"
        if authority_field == "capacity_class_id"
        else profile.capacity_class_id,
    )
    witnesses = tuple(
        replace(item, execution_profile_id="python-privileged")
        if item.witness_id == "backend-witness"
        else item
        for item in catalog.witnesses
    )
    policy = replace(
        make_policy_value(
            catalog=replace(
                catalog,
                witnesses=witnesses,
                execution_profiles=(*catalog.execution_profiles, privileged_profile),
            )
        ),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    candidate = _candidate(plan(input, policy))

    verified = verify(
        input,
        policy,
        candidate,
        _envelope(input.input_hash, _advice(add_obligations=["backend-tests"])),
    )

    assert verified.fallback.triggered is True
    assert verified.fallback.reason == "agent_advice_authority_expansion"
    assert verified.agent_advice is not None
    assert verified.agent_advice.accepted is False
    assert verified.agent_advice.reasons == ("agent_advice_authority_expansion",)


@pytest.mark.parametrize("recommendation", (False, True), ids=("rejection", "recommendation"))
def test_fallback_is_independent_of_a_planner_that_returns_an_incomplete_catalog(
    monkeypatch: pytest.MonkeyPatch,
    recommendation: bool,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    reason = "agent_advice_recommended_full_ci" if recommendation else "deterministic_plan_mismatch"
    expected = full_ci_fallback_plan(input, policy, reason)
    malformed = _with_selection(expected, (), ())
    candidate = _candidate(plan(input, policy)) if recommendation else malformed
    advice = (
        _envelope(input.input_hash, _advice(fallback_recommendation="full-ci"))
        if recommendation
        else None
    )
    assert admit_deterministic_plan(input, policy, malformed) == "deterministic_plan_mismatch"
    monkeypatch.setattr(planner_module, "_full_ci_plan", lambda *_args: malformed)

    verified = verify(input, policy, candidate, advice)

    assert verified.fallback.triggered is True
    assert verified.fallback.reason == reason
    assert verified.omitted_obligations == ()
    assert {item.obligation_id: item.depth for item in verified.selected_obligations} == {
        "backend-tests": "full",
        "docs-lint": "standard",
        "required-baseline": "full",
    }
    assert verified.source_plan == expected
    assert verified.source_plan.plan_id == expected.plan_id
    assert verified.source_plan.identity_mapping() == expected.identity_mapping()


def test_advice_closure_does_not_execute_planner_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    candidate = _candidate(plan(input, policy))

    def unexpected_planner_call(_depth: object) -> NoReturn:
        raise AssertionError("verification executed planner witness closure")

    monkeypatch.setattr(planner_module, "depth_rank", unexpected_planner_call)

    verified = verify(
        input,
        policy,
        candidate,
        _envelope(input.input_hash, _advice(add_obligations=["backend-tests"])),
    )

    assert verified.fallback.triggered is False
    assert verified.agent_advice is not None
    assert verified.agent_advice.accepted is True
    assert verified.selected_witnesses == (
        SelectedWitness("backend-witness", "targeted", ("backend-tests",)),
        SelectedWitness("baseline-witness", "standard", ("required-baseline",)),
        SelectedWitness("docs-witness", "smoke", ("docs-lint",)),
        SelectedWitness("shared-quality", "targeted", ("backend-tests", "docs-lint")),
    )


def test_verified_model_rejects_missing_added_witnesses_despite_shared_validator_defect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    candidate = _candidate(plan(input, policy))
    selected = (
        SelectedObligation("backend-tests", "targeted", ("backend-witness", "shared-quality")),
        *candidate.selected_obligations,
    )
    monkeypatch.setattr(
        verified_model, "validate_witness_closure", lambda *_args: None, raising=False
    )

    with pytest.raises(ValueError, match="verified witnesses must exactly close"):
        make_verified_plan(
            source_plan=candidate,
            catalog=policy.catalog,
            selected_obligations=selected,
            selected_witnesses=candidate.selected_witnesses,
            omitted_obligations=(),
            fallback=candidate.fallback,
            deterministic_evidence=candidate.evidence,
            verification_evidence=(),
            agent_advice=None,
        )


@pytest.mark.parametrize("incomplete", (False, True), ids=("depth", "catalog"))
def test_verified_model_rejects_structurally_valid_but_inadequate_full_ci(incomplete: bool) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    source = full_ci_fallback_plan(input, policy, "provider_unavailable")
    selected = (
        ()
        if incomplete
        else tuple(
            SelectedObligation(item.obligation_id, item.default_depth, item.required_witness_ids)
            for item in policy.catalog.obligations
        )
    )
    witnesses = close_selected_witnesses(policy.catalog, selected)
    source = _with_selection(source, selected, witnesses)
    reason = "classify every catalog obligation" if incomplete else "full catalog at full depth"

    with pytest.raises(ValueError, match=reason):
        make_verified_plan(
            source_plan=source,
            catalog=policy.catalog,
            selected_obligations=selected,
            selected_witnesses=witnesses,
            omitted_obligations=(),
            fallback=source.fallback,
            deterministic_evidence=source.evidence,
            verification_evidence=(),
            agent_advice=None,
        )


def test_advice_cannot_exceed_the_obligation_full_depth_cap() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    candidate = full_ci_fallback_plan(input, policy, "provider_unavailable")

    verified = verify(
        input,
        policy,
        candidate,
        _envelope(
            input.input_hash,
            _advice(
                increase_depth=[{"obligationId": "required-baseline", "minDepth": "exhaustive"}]
            ),
        ),
    )

    assert verified.fallback == candidate.fallback
    assert verified.agent_advice is not None
    assert verified.agent_advice.accepted is False
    assert verified.agent_advice.reasons == ("agent_advice_depth_unsupported",)
    assert verified.selected_obligations == candidate.selected_obligations
    assert verified.selected_witnesses == candidate.selected_witnesses


def test_verified_model_preserves_stronger_full_ci_without_granting_advice_authority() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    candidate = full_ci_fallback_plan(input, policy, "provider_unavailable")
    selected = tuple(
        replace(item, depth="exhaustive") if item.obligation_id == "required-baseline" else item
        for item in candidate.selected_obligations
    )
    witnesses = tuple(
        replace(item, depth="exhaustive") if item.witness_id == "baseline-witness" else item
        for item in candidate.selected_witnesses
    )

    verified = make_verified_plan(
        source_plan=candidate,
        catalog=policy.catalog,
        selected_obligations=selected,
        selected_witnesses=witnesses,
        omitted_obligations=(),
        fallback=candidate.fallback,
        deterministic_evidence=candidate.evidence,
        verification_evidence=(),
        agent_advice=None,
    )

    assert verified.agent_advice is None
    assert verified.fallback == candidate.fallback
    assert verified.selected_obligations[-1].depth == "exhaustive"
    assert (
        next(
            item for item in verified.selected_witnesses if item.witness_id == "baseline-witness"
        ).depth
        == "exhaustive"
    )


@pytest.mark.parametrize(
    ("increase_depth", "risk_findings", "obligation_id", "depth", "evidence_detail"),
    [
        (
            [{"obligationId": "docs-lint", "minDepth": "standard"}],
            None,
            "docs-lint",
            "standard",
            "depth:docs-lint",
        ),
        (
            None,
            [{"obligationId": "backend-tests", "reason": "shared code"}],
            "backend-tests",
            "targeted",
            "risk:backend-tests",
        ),
    ],
    ids=("depth", "risk"),
)
def test_agent_depth_and_risk_effects_only_increase_coverage(
    increase_depth: list[dict[str, str]] | None,
    risk_findings: list[dict[str, str]] | None,
    obligation_id: str,
    depth: str,
    evidence_detail: str,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    candidate = _candidate(plan(input, policy))

    verified = verify(
        input,
        policy,
        candidate,
        _envelope(
            input.input_hash,
            _advice(increase_depth=increase_depth, risk_findings=risk_findings),
        ),
    )

    selected = {item.obligation_id: item.depth for item in verified.selected_obligations}
    assert selected[obligation_id] == depth
    assert any(item.message == evidence_detail for item in verified.verification_evidence)
    assert verified.fallback.triggered is False


def test_agent_full_ci_recommendation_cannot_reduce_validation() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )
    candidate = _candidate(plan(input, policy))

    verified = verify(
        input,
        policy,
        candidate,
        _envelope(input.input_hash, _advice(fallback_recommendation="full-ci")),
    )

    assert verified.fallback.triggered is True
    assert verified.fallback.reason == "agent_advice_recommended_full_ci"
    assert {item.obligation_id for item in verified.selected_obligations} == {
        "backend-tests",
        "docs-lint",
        "required-baseline",
    }


def test_omission_validator_rejects_a_tampered_catalog_binding() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    candidate = _candidate(plan(input, policy))
    omitted = candidate.omitted_obligations[0]
    tampered = replace(
        omitted,
        proof=replace(omitted.proof, catalog_hash="f" * 64),
    )

    assert validate_omission_proof(input, policy, omitted) is True
    assert validate_omission_proof(input, policy, tampered) is False


def test_verifier_rejects_a_defect_reproduced_by_planner_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    candidate = _candidate(plan(input, policy))
    omitted = candidate.omitted_obligations[0]
    tampered_omitted = replace(
        omitted,
        proof=replace(omitted.proof, catalog_hash="f" * 64),
    )
    tampered_omissions = (tampered_omitted, *candidate.omitted_obligations[1:])
    identity = candidate.identity_mapping()
    identity["omittedObligations"] = [item.to_identity_mapping() for item in tampered_omissions]
    tampered_plan = replace(
        candidate,
        plan_id="dynamic_ci_plan_" + hash_object(identity)[:32],
        omitted_obligations=tampered_omissions,
    )
    original_builder = build_omission_proof

    def defective_builder(
        input: PlanningInput,
        policy: PlanningPolicy,
        obligation: ValidationObligation,
        impact: ImpactAnalysis,
    ) -> OmissionProof | NotOmittable:
        decision = original_builder(input, policy, obligation, impact)
        if isinstance(decision, OmissionProof) and decision.obligation_id == omitted.obligation_id:
            return replace(decision, catalog_hash=tampered_omitted.proof.catalog_hash)
        return decision

    monkeypatch.setattr(planner_module, "build_omission_proof", defective_builder)
    assert tampered_omitted.proof.catalog_hash != policy.catalog_hash
    assert _candidate(plan(input, policy)) == tampered_plan

    verified = verify(input, policy, tampered_plan)

    assert verified.fallback.triggered is True
    assert verified.fallback.reason == "omission_proof_mismatch"


def test_omission_validator_rejects_impact_even_with_current_proof_coordinates() -> None:
    policy = make_policy_value()
    source = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    impacted = make_input_value(
        DiffFileChangeInput(path="src/service/component.py", status="modified")
    )
    omitted = next(
        item
        for item in _candidate(plan(source, policy)).omitted_obligations
        if item.obligation_id == "backend-tests"
    )
    forged = replace(
        omitted,
        proof=replace(
            omitted.proof,
            repo_epoch_hash=hash_object(impacted.repo_epoch.to_identity_mapping()),
            diff_hash=impacted.diff.diff_hash,
            policy_hash=policy.policy_hash,
            graph_hash=impacted.dependency_graph.graph_hash,
            catalog_hash=policy.catalog_hash,
        ),
    )

    assert validate_omission_proof(impacted, policy, forged) is False


def test_coverage_order_rejects_lower_obligation_and_witness_depth() -> None:
    input = make_input_value(DiffFileChangeInput(path="ci/pipeline.yml", status="modified"))
    policy = make_policy_value()
    candidate = _candidate(plan(input, policy))
    lowered_obligations = tuple(
        replace(item, depth="smoke") if item.obligation_id == "backend-tests" else item
        for item in candidate.selected_obligations
    )
    lowered_witnesses = close_selected_witnesses(policy.catalog, lowered_obligations)

    comparison = compare_coverage(
        candidate.selected_obligations,
        candidate.selected_witnesses,
        lowered_obligations,
        lowered_witnesses,
    )

    assert comparison.relation == "less"
    assert "obligation_depth_lowered:backend-tests" in comparison.reasons


def test_verified_plan_rejects_catalog_from_another_config_epoch() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    changed_profile = replace(
        policy.catalog.execution_profiles[0],
        fixture_profile_id="integration",
    )
    changed_catalog = replace(policy.catalog, execution_profiles=(changed_profile,))

    with pytest.raises(ValueError, match="source plan catalog hash"):
        replace(verified, catalog=changed_catalog)


def _advice(
    *,
    add_obligations: list[str] | None = None,
    increase_depth: list[dict[str, str]] | None = None,
    risk_findings: list[dict[str, str]] | None = None,
    fallback_recommendation: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schemaVersion": "agent-risk-advice/v2",
        "adviceId": "advice-1",
        "confidence": 0.9,
        "addObligations": add_obligations or [],
        "increaseDepth": increase_depth or [],
        "riskFindings": risk_findings or [],
        "rationale": "conservative expansion",
    }
    if fallback_recommendation is not None:
        result["fallbackRecommendation"] = fallback_recommendation
    return result


_PROMPT_HASH = "e" * 64


def _envelope(input_hash: str, output: object) -> AdviceExecutionEnvelope:
    return _envelope_bytes(input_hash, canonical_json(output))


def _envelope_bytes(input_hash: str, raw_output: bytes) -> AdviceExecutionEnvelope:
    return AdviceExecutionEnvelope(
        raw_output=raw_output,
        input_hash=input_hash,
        model_id="model",
        prompt_hash=_PROMPT_HASH,
        evaluation=AdviceEvaluationEvidence(
            output_hash=hashlib.sha256(raw_output).hexdigest(),
            evaluator_id="prompt-injection-evaluator/v1",
            policy_hash="f" * 64,
            passed=True,
        ),
    )


def _candidate(value: DeterministicPlan | PlanningRejected) -> DeterministicPlan:
    if isinstance(value, PlanningRejected):
        pytest.fail("unexpected planning rejection")
    return value


def _with_selection(
    source: DeterministicPlan,
    selected: tuple[SelectedObligation, ...],
    witnesses: tuple[SelectedWitness, ...],
) -> DeterministicPlan:
    identity = source.identity_mapping()
    identity["selectedObligations"] = [item.to_identity_mapping() for item in selected]
    identity["selectedWitnesses"] = [item.to_identity_mapping() for item in witnesses]
    return replace(
        source,
        plan_id="dynamic_ci_plan_" + hash_object(identity)[:32],
        selected_obligations=selected,
        selected_witnesses=witnesses,
    )
