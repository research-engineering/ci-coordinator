from __future__ import annotations

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.planning_core.impact import analyze_impact
from ci_coordinator.planning_core.model import (
    DeterministicPlan,
    OmittedObligation,
    PlanEvidence,
    PlanFallback,
    PlanningRejected,
    PlanningResult,
    SelectedObligation,
    SelectedWitness,
)
from ci_coordinator.planning_core.omission_proof import NotOmittable, build_omission_proof
from ci_coordinator.planning_core.policy import (
    PlanningPolicy,
    planner_admission_reasons,
    planner_rejection_reasons,
)
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.validation_contract import ValidationCatalog, depth_rank


def plan(input: PlanningInput, policy: PlanningPolicy) -> PlanningResult:
    rejection_reasons = planner_rejection_reasons(input, policy)
    if rejection_reasons:
        return PlanningRejected(rejection_reasons)
    admission_reasons = planner_admission_reasons(input, policy)
    if admission_reasons:
        return _full_ci_plan(input, policy, "; ".join(admission_reasons))
    impact = analyze_impact(input)
    if impact.unknown_paths:
        return _full_ci_plan(
            input,
            policy,
            "unknown changed paths: " + ", ".join(impact.unknown_paths),
        )

    selected: list[SelectedObligation] = []
    omitted: list[OmittedObligation] = []
    evidence: list[PlanEvidence] = []
    for obligation in policy.catalog.obligations:
        decision = build_omission_proof(input, policy, obligation, impact)
        if isinstance(decision, NotOmittable):
            selected.append(
                SelectedObligation(
                    obligation.obligation_id,
                    obligation.default_depth,
                    obligation.required_witness_ids,
                )
            )
            evidence.append(PlanEvidence("selected", obligation.obligation_id, decision.reason))
            continue
        omitted.append(
            OmittedObligation(
                obligation.obligation_id,
                obligation.required_witness_ids,
                decision,
            )
        )
        evidence.append(
            PlanEvidence(
                "omitted",
                obligation.obligation_id,
                "deterministic proof showed no impact on this obligation",
            )
        )
    return _finalize_plan(
        input,
        policy,
        selected_obligations=tuple(selected),
        omitted_obligations=tuple(omitted),
        fallback=PlanFallback(policy.fallback_timeout_seconds, False, None),
        evidence=tuple(evidence),
    )


def full_ci_fallback_plan(
    input: PlanningInput,
    policy: PlanningPolicy,
    reason: str,
) -> DeterministicPlan:
    """Build a conservative plan that selects every obligation at full depth."""

    return _full_ci_plan(input, policy, reason)


def close_selected_witnesses(
    catalog: ValidationCatalog,
    selected_obligations: tuple[SelectedObligation, ...],
) -> tuple[SelectedWitness, ...]:
    """Close selected obligations to their witnesses at the strongest required depth."""

    obligations = {item.obligation_id: item for item in catalog.obligations}
    witnesses = {item.witness_id: item for item in catalog.witnesses}
    required_by: dict[str, list[SelectedObligation]] = {}
    for selected in selected_obligations:
        obligation = obligations.get(selected.obligation_id)
        if obligation is None:
            raise ValueError("selected obligation is absent from the validation catalog")
        for witness_id in obligation.required_witness_ids:
            witness = witnesses[witness_id]
            if selected.depth not in witness.supported_depths:
                raise ValueError("selected depth is unsupported by a required witness")
            required_by.setdefault(witness_id, []).append(selected)

    return tuple(
        SelectedWitness(
            witness_id=witness_id,
            depth=max((item.depth for item in requirements), key=depth_rank),
            required_by_obligation_ids=tuple(
                sorted(
                    (item.obligation_id for item in requirements),
                    key=utf16_sort_key,
                )
            ),
        )
        for witness_id, requirements in sorted(
            required_by.items(), key=lambda item: utf16_sort_key(item[0])
        )
    )


def _full_ci_plan(input: PlanningInput, policy: PlanningPolicy, reason: str) -> DeterministicPlan:
    selected = tuple(
        SelectedObligation(
            obligation.obligation_id,
            obligation.full_depth,
            obligation.required_witness_ids,
        )
        for obligation in policy.catalog.obligations
    )
    return _finalize_plan(
        input,
        policy,
        selected_obligations=selected,
        omitted_obligations=(),
        fallback=PlanFallback(policy.fallback_timeout_seconds, True, reason),
        evidence=(PlanEvidence("fallback", None, reason),),
    )


def _finalize_plan(
    input: PlanningInput,
    policy: PlanningPolicy,
    *,
    selected_obligations: tuple[SelectedObligation, ...],
    omitted_obligations: tuple[OmittedObligation, ...],
    fallback: PlanFallback,
    evidence: tuple[PlanEvidence, ...],
) -> DeterministicPlan:
    selected = tuple(
        sorted(selected_obligations, key=lambda item: utf16_sort_key(item.obligation_id))
    )
    witnesses = close_selected_witnesses(policy.catalog, selected)
    omitted = tuple(
        sorted(omitted_obligations, key=lambda item: utf16_sort_key(item.obligation_id))
    )
    ordered_evidence = tuple(
        sorted(
            evidence,
            key=lambda item: utf16_sort_key(
                item.kind + ":" + (item.obligation_id or "") + ":" + item.message
            ),
        )
    )
    identity = {
        "schemaVersion": "deterministic-plan/v1",
        "plannerVersion": "planning-core/v1",
        "configEpochId": input.policy.epoch_id,
        "repoEpochHash": hash_object(input.repo_epoch.to_identity_mapping()),
        "inputHash": input.input_hash,
        "diffHash": input.diff.diff_hash,
        "compiledPolicyHash": policy.compiled_policy_hash,
        "policyHash": policy.policy_hash,
        "dependencyGraphHash": input.dependency_graph.graph_hash,
        "catalogHash": policy.catalog_hash,
        "selectedObligations": [item.to_identity_mapping() for item in selected],
        "selectedWitnesses": [item.to_identity_mapping() for item in witnesses],
        "omittedObligations": [item.to_identity_mapping() for item in omitted],
        "fallback": fallback.to_identity_mapping(),
        "evidence": [item.to_identity_mapping() for item in ordered_evidence],
    }
    return DeterministicPlan(
        plan_id="dynamic_ci_plan_" + hash_object(identity)[:32],
        schema_version="deterministic-plan/v1",
        planner_version="planning-core/v1",
        config_epoch_id=input.policy.epoch_id,
        repo_epoch_hash=hash_object(input.repo_epoch.to_identity_mapping()),
        input_hash=input.input_hash,
        diff_hash=input.diff.diff_hash,
        compiled_policy_hash=policy.compiled_policy_hash,
        policy_hash=policy.policy_hash,
        dependency_graph_hash=input.dependency_graph.graph_hash,
        catalog_hash=policy.catalog_hash,
        selected_obligations=selected,
        selected_witnesses=witnesses,
        omitted_obligations=omitted,
        fallback=fallback,
        evidence=ordered_evidence,
    )
