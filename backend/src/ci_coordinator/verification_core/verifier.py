from __future__ import annotations

from dataclasses import replace

from ci_coordinator.agent_risk_advice import (
    AdmittedAdvice,
    AdviceAuditMetadata,
    AdviceExecutionEnvelope,
    RejectedAdvice,
    admit_advice,
)
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.planning_core.model import (
    DeterministicPlan,
    PlanEvidence,
    PlanFallback,
    SelectedObligation,
    SelectedWitness,
)
from ci_coordinator.planning_core.policy import PlanningPolicy
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.validation_contract import ValidationCatalog, ValidationObligation, max_depth
from ci_coordinator.verification_core.coverage import compare_coverage
from ci_coordinator.verification_core.deterministic_admission import (
    admit_deterministic_plan,
)
from ci_coordinator.verification_core.model import (
    VerificationEvidence,
    VerifiedPlan,
    make_verified_plan,
)
from ci_coordinator.verification_core.witnesses import close_selected_witnesses


def verify(
    input: PlanningInput,
    policy: PlanningPolicy,
    deterministic_plan: DeterministicPlan,
    agent_advice: AdviceExecutionEnvelope | None = None,
) -> VerifiedPlan:
    rejection_reason = admit_deterministic_plan(input, policy, deterministic_plan)
    if rejection_reason is not None:
        return _fallback(input, policy, rejection_reason, None)
    if agent_advice is None:
        return _base(deterministic_plan, policy.catalog, None, ())
    admission = admit_advice(agent_advice, input=input, policy=policy)
    if isinstance(admission, RejectedAdvice):
        return _base(
            deterministic_plan,
            policy.catalog,
            admission.audit,
            (VerificationEvidence("agent_advice_rejected", ";".join(admission.audit.reasons)),),
        )
    return _apply_advice(input, policy, deterministic_plan, admission)


def _base(
    plan_value: DeterministicPlan,
    catalog: ValidationCatalog,
    advice: AdviceAuditMetadata | None,
    evidence: tuple[VerificationEvidence, ...],
) -> VerifiedPlan:
    if advice is not None and type(advice) is not AdviceAuditMetadata:
        raise TypeError("advice audit metadata must be typed")
    return make_verified_plan(
        source_plan=plan_value,
        catalog=catalog,
        selected_obligations=plan_value.selected_obligations,
        selected_witnesses=plan_value.selected_witnesses,
        omitted_obligations=plan_value.omitted_obligations,
        fallback=plan_value.fallback,
        deterministic_evidence=plan_value.evidence,
        verification_evidence=(
            VerificationEvidence("deterministic", "independently admitted deterministic plan"),
            *evidence,
        ),
        agent_advice=advice,
    )


def _fallback(
    input: PlanningInput,
    policy: PlanningPolicy,
    reason: str,
    advice: AdviceAuditMetadata | None,
) -> VerifiedPlan:
    fallback = _full_ci_fallback_plan(input, policy, reason)
    if advice is not None and type(advice) is not AdviceAuditMetadata:
        raise TypeError("advice audit metadata must be typed")
    return make_verified_plan(
        source_plan=fallback,
        catalog=policy.catalog,
        selected_obligations=fallback.selected_obligations,
        selected_witnesses=fallback.selected_witnesses,
        omitted_obligations=fallback.omitted_obligations,
        fallback=fallback.fallback,
        deterministic_evidence=fallback.evidence,
        verification_evidence=(VerificationEvidence("deterministic", reason),),
        agent_advice=advice,
    )


def _apply_advice(
    input: PlanningInput,
    policy: PlanningPolicy,
    base: DeterministicPlan,
    admitted: AdmittedAdvice,
) -> VerifiedPlan:
    advice = admitted.advice
    if advice.fallback_recommendation == "full-ci":
        reason = "agent_advice_recommended_full_ci"
        fallback = _full_ci_fallback_plan(input, policy, reason)
        return make_verified_plan(
            source_plan=fallback,
            catalog=policy.catalog,
            selected_obligations=fallback.selected_obligations,
            selected_witnesses=fallback.selected_witnesses,
            omitted_obligations=fallback.omitted_obligations,
            fallback=fallback.fallback,
            deterministic_evidence=fallback.evidence,
            verification_evidence=(
                VerificationEvidence("deterministic", reason),
                VerificationEvidence("agent_advice_accepted", reason),
            ),
            agent_advice=admitted.audit,
        )

    obligations = {item.obligation_id: item for item in policy.catalog.obligations}
    selected = {item.obligation_id: item for item in base.selected_obligations}
    evidence: list[VerificationEvidence] = []
    for obligation_id in advice.add_obligations:
        selected.setdefault(obligation_id, _selected(obligations[obligation_id]))
        evidence.append(VerificationEvidence("agent_advice_accepted", "added:" + obligation_id))
    for increase in advice.increase_depth:
        obligation = obligations[increase.obligation_id]
        current = selected.get(increase.obligation_id, _selected(obligation))
        selected[increase.obligation_id] = SelectedObligation(
            obligation_id=current.obligation_id,
            depth=max_depth(current.depth, increase.min_depth),
            required_witness_ids=current.required_witness_ids,
        )
        evidence.append(
            VerificationEvidence(
                "agent_advice_accepted",
                "depth:" + increase.obligation_id,
            )
        )
    for finding in advice.risk_findings:
        selected.setdefault(
            finding.obligation_id,
            _selected(obligations[finding.obligation_id]),
        )
        evidence.append(
            VerificationEvidence(
                "agent_advice_accepted",
                "risk:" + finding.obligation_id,
            )
        )

    final_selected = tuple(
        sorted(selected.values(), key=lambda item: utf16_sort_key(item.obligation_id))
    )
    final_witnesses = close_selected_witnesses(policy.catalog, final_selected)
    if not _execution_authorities(policy.catalog, final_witnesses).issubset(
        _execution_authorities(policy.catalog, base.selected_witnesses)
    ):
        reason = "agent_advice_authority_expansion"
        return _fallback(input, policy, reason, _rejected_audit(admitted.audit, reason))
    comparison = compare_coverage(
        base.selected_obligations,
        base.selected_witnesses,
        final_selected,
        final_witnesses,
    )
    if comparison.relation in {"less", "incomparable"}:
        reason = "agent_advice_coverage_regression"
        return _fallback(input, policy, reason, _rejected_audit(admitted.audit, reason))
    selected_ids = {item.obligation_id for item in final_selected}
    final_omitted = tuple(
        item for item in base.omitted_obligations if item.obligation_id not in selected_ids
    )
    return make_verified_plan(
        source_plan=base,
        catalog=policy.catalog,
        selected_obligations=final_selected,
        selected_witnesses=final_witnesses,
        omitted_obligations=final_omitted,
        fallback=base.fallback,
        deterministic_evidence=base.evidence,
        verification_evidence=(
            VerificationEvidence("deterministic", "independently admitted deterministic plan"),
            *evidence,
        ),
        agent_advice=admitted.audit,
    )


def _full_ci_fallback_plan(
    input: PlanningInput,
    policy: PlanningPolicy,
    reason: str,
) -> DeterministicPlan:
    selected = tuple(
        SelectedObligation(item.obligation_id, item.full_depth, item.required_witness_ids)
        for item in policy.catalog.obligations
    )
    witnesses = close_selected_witnesses(policy.catalog, selected)
    fallback = PlanFallback(policy.fallback_timeout_seconds, True, reason)
    evidence = (PlanEvidence("fallback", None, reason),)
    repo_epoch_hash = hash_object(input.repo_epoch.to_identity_mapping())
    identity = {
        "schemaVersion": "deterministic-plan/v1",
        "plannerVersion": "planning-core/v1",
        "configEpochId": input.policy.epoch_id,
        "repoEpochHash": repo_epoch_hash,
        "inputHash": input.input_hash,
        "diffHash": input.diff.diff_hash,
        "compiledPolicyHash": policy.compiled_policy_hash,
        "policyHash": policy.policy_hash,
        "dependencyGraphHash": input.dependency_graph.graph_hash,
        "catalogHash": policy.catalog_hash,
        "selectedObligations": [item.to_identity_mapping() for item in selected],
        "selectedWitnesses": [item.to_identity_mapping() for item in witnesses],
        "omittedObligations": [],
        "fallback": fallback.to_identity_mapping(),
        "evidence": [item.to_identity_mapping() for item in evidence],
    }
    return DeterministicPlan(
        plan_id="dynamic_ci_plan_" + hash_object(identity)[:32],
        schema_version="deterministic-plan/v1",
        planner_version="planning-core/v1",
        config_epoch_id=input.policy.epoch_id,
        repo_epoch_hash=repo_epoch_hash,
        input_hash=input.input_hash,
        diff_hash=input.diff.diff_hash,
        compiled_policy_hash=policy.compiled_policy_hash,
        policy_hash=policy.policy_hash,
        dependency_graph_hash=input.dependency_graph.graph_hash,
        catalog_hash=policy.catalog_hash,
        selected_obligations=selected,
        selected_witnesses=witnesses,
        omitted_obligations=(),
        fallback=fallback,
        evidence=evidence,
    )


def _selected(obligation: ValidationObligation) -> SelectedObligation:
    return SelectedObligation(
        obligation_id=obligation.obligation_id,
        depth=obligation.default_depth,
        required_witness_ids=obligation.required_witness_ids,
    )


def _execution_authorities(
    catalog: ValidationCatalog,
    witnesses: tuple[SelectedWitness, ...],
) -> frozenset[tuple[str, str, str, str, tuple[str, ...], str]]:
    witness_profiles = {item.witness_id: item.execution_profile_id for item in catalog.witnesses}
    profiles = {item.profile_id: item for item in catalog.execution_profiles}
    return frozenset(
        (
            profile.runner_profile_id,
            profile.permission_profile_id,
            profile.credential_profile_id,
            profile.fixture_profile_id,
            profile.service_profile_ids,
            profile.capacity_class_id,
        )
        for witness in witnesses
        for profile in (profiles[witness_profiles[witness.witness_id]],)
    )


def _rejected_audit(audit: AdviceAuditMetadata, reason: str) -> AdviceAuditMetadata:
    return replace(
        audit,
        accepted=False,
        reasons=tuple(sorted({*audit.reasons, reason}, key=utf16_sort_key)),
    )
