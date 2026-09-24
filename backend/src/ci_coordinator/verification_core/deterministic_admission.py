"""Independent admission of deterministic coverage plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.planning_core.model import (
    DeterministicPlan,
    OmissionPredicates,
    OmittedObligation,
    PlanEvidence,
    SelectedObligation,
)
from ci_coordinator.planning_core.policy import PlanningPolicy
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.repo_context.freshness import matches_path_pattern
from ci_coordinator.validation_contract import (
    ValidationCatalog,
    ValidationObligation,
)
from ci_coordinator.verification_core.witnesses import close_selected_witnesses

type DeterministicPlanRejectionReason = Literal[
    "deterministic_plan_mismatch",
    "omission_proof_mismatch",
]

_OMISSION_ASSUMPTIONS = tuple(
    sorted(
        {
            "dependency graph includes all known dependents for changed paths",
            "validation catalog is closed over obligation and witness identities",
        },
        key=utf16_sort_key,
    )
)
_OMISSION_INVALIDATORS = tuple(
    sorted(
        {
            "changed path is unknown",
            "dependency graph is stale",
            "diff is truncated",
            "policy changes",
            "validation catalog changes",
        },
        key=utf16_sort_key,
    )
)


@dataclass(frozen=True, slots=True)
class _ImpactFacts:
    impacted_paths: frozenset[str]
    impacted_risk_classes: frozenset[str]
    unknown_paths: frozenset[str]
    global_risk_paths: frozenset[str]


def admit_deterministic_plan(
    input: PlanningInput,
    policy: PlanningPolicy,
    candidate: DeterministicPlan,
) -> DeterministicPlanRejectionReason | None:
    """Return the conservative fallback reason, or admit the candidate."""

    if not _coordinates_are_current(input, policy, candidate):
        return "deterministic_plan_mismatch"
    if candidate.fallback.triggered:
        return None if _is_exact_full_ci(candidate, policy) else "deterministic_plan_mismatch"
    if not _selective_context_is_admitted(input):
        return "deterministic_plan_mismatch"
    if (
        candidate.fallback.timeout_seconds != policy.fallback_timeout_seconds
        or not _is_complete_selective_structure(candidate, policy.catalog)
    ):
        return "deterministic_plan_mismatch"

    impact = _derive_impact(input)
    if impact.unknown_paths or impact.global_risk_paths:
        return "omission_proof_mismatch"
    if not _omissions_are_valid(
        input,
        policy,
        candidate.omitted_obligations,
        impact,
    ):
        return "omission_proof_mismatch"
    return None


def validate_omission_proof(
    input: PlanningInput,
    policy: PlanningPolicy,
    omitted: OmittedObligation,
) -> bool:
    if not _input_coordinates_are_current(input, policy) or not _selective_context_is_admitted(
        input
    ):
        return False
    impact = _derive_impact(input)
    obligations = {item.obligation_id: item for item in policy.catalog.obligations}
    return (
        not impact.unknown_paths
        and not impact.global_risk_paths
        and _omission_is_valid(
            input,
            policy,
            omitted,
            impact,
            obligations=obligations,
            repo_epoch_hash=hash_object(input.repo_epoch.to_identity_mapping()),
        )
    )


def _coordinates_are_current(
    input: PlanningInput,
    policy: PlanningPolicy,
    candidate: DeterministicPlan,
) -> bool:
    repo_epoch_hash = hash_object(input.repo_epoch.to_identity_mapping())
    return (
        _input_coordinates_are_current(input, policy)
        and candidate.plan_id == "dynamic_ci_plan_" + hash_object(candidate.identity_mapping())[:32]
        and candidate.config_epoch_id == input.policy.epoch_id
        and candidate.repo_epoch_hash == repo_epoch_hash
        and candidate.input_hash == input.input_hash
        and candidate.diff_hash == input.diff.diff_hash
        and candidate.compiled_policy_hash == policy.compiled_policy_hash
        and candidate.policy_hash == policy.policy_hash
        and candidate.dependency_graph_hash == input.dependency_graph.graph_hash
        and candidate.catalog_hash == policy.catalog_hash
    )


def _input_coordinates_are_current(input: PlanningInput, policy: PlanningPolicy) -> bool:
    recomputed_diff_hash = hash_object(input.diff.to_identity_mapping())
    recomputed_input_hash = hash_object(
        {
            "repoEpoch": input.repo_epoch.to_identity_mapping(),
            "configEpochId": input.policy.epoch_id,
            "compiledPolicyHash": input.policy.compiled_policy_hash,
            "diffHash": input.diff.diff_hash,
            "policyHash": input.policy.policy_hash,
            "dependencyGraphHash": input.dependency_graph.graph_hash,
        }
    )
    return (
        _policy_context_is_current(input, policy)
        and input.diff.diff_hash == recomputed_diff_hash
        and input.input_hash == recomputed_input_hash
    )


def _selective_context_is_admitted(input: PlanningInput) -> bool:
    graph = input.dependency_graph
    repo_epoch_hash = hash_object(input.repo_epoch.to_identity_mapping())
    return (
        not input.fallback_reasons
        and not input.diff.invalidating_reasons
        and not input.diff.truncated
        and input.diff.source.complete
        and input.diff.source.file_count == len(input.diff.files)
        and input.diff.source.file_count <= input.diff.source.max_files
        and not graph.invalidating_reasons
        and graph.fresh
        and input.repo_epoch.base_sha == input.diff.base_sha
        and input.repo_epoch.head_sha == input.diff.head_sha
        and graph.admitted_repo_epoch_hash == repo_epoch_hash
        and graph.admitted_diff_hash == input.diff.diff_hash
        and graph.admitted_config_epoch_id == input.policy.epoch_id
        and graph.admitted_policy_hash == input.policy.policy_hash
        and graph.admitted_compiled_policy_hash == input.policy.compiled_policy_hash
    )


def _policy_context_is_current(
    input: PlanningInput,
    policy: PlanningPolicy,
) -> bool:
    return (
        policy.config_epoch_id == input.policy.epoch_id
        and policy.compiled_policy_hash == input.policy.compiled_policy_hash
        and policy.policy_hash == input.policy.policy_hash
    )


def _is_exact_full_ci(
    candidate: DeterministicPlan,
    policy: PlanningPolicy,
) -> bool:
    expected_selected = _expected_selected(policy.catalog, full=True)
    return (
        candidate.fallback.timeout_seconds == policy.fallback_timeout_seconds
        and candidate.fallback.reason is not None
        and candidate.selected_obligations == expected_selected
        and candidate.selected_witnesses
        == close_selected_witnesses(policy.catalog, expected_selected)
        and not candidate.omitted_obligations
        and candidate.evidence == (PlanEvidence("fallback", None, candidate.fallback.reason),)
    )


def _is_complete_selective_structure(
    candidate: DeterministicPlan,
    catalog: ValidationCatalog,
) -> bool:
    obligations = {item.obligation_id: item for item in catalog.obligations}
    selected_ids = {item.obligation_id for item in candidate.selected_obligations}
    omitted_ids = {item.obligation_id for item in candidate.omitted_obligations}
    if (
        candidate.fallback.reason is not None
        or selected_ids & omitted_ids
        or selected_ids | omitted_ids != set(obligations)
    ):
        return False
    if any(
        selected.required_witness_ids != obligations[selected.obligation_id].required_witness_ids
        or selected.depth != obligations[selected.obligation_id].default_depth
        for selected in candidate.selected_obligations
    ):
        return False
    if candidate.selected_witnesses != close_selected_witnesses(
        catalog,
        candidate.selected_obligations,
    ):
        return False
    evidence_by_obligation: dict[str, PlanEvidence] = {}
    for evidence in candidate.evidence:
        if (
            evidence.kind == "fallback"
            or evidence.obligation_id is None
            or evidence.obligation_id in evidence_by_obligation
        ):
            return False
        evidence_by_obligation[evidence.obligation_id] = evidence
    return set(evidence_by_obligation) == set(obligations) and all(
        evidence_by_obligation[obligation_id].kind
        == ("selected" if obligation_id in selected_ids else "omitted")
        for obligation_id in obligations
    )


def _expected_selected(
    catalog: ValidationCatalog,
    *,
    full: bool,
) -> tuple[SelectedObligation, ...]:
    return tuple(
        SelectedObligation(
            obligation.obligation_id,
            obligation.full_depth if full else obligation.default_depth,
            obligation.required_witness_ids,
        )
        for obligation in catalog.obligations
    )


def _derive_impact(input: PlanningInput) -> _ImpactFacts:
    nodes = {node.path: node for node in input.dependency_graph.nodes}
    changed_paths = set(input.diff.changed_paths)
    impacted_paths = set(changed_paths)
    impacted_risk_classes: set[str] = set()
    unknown_paths = changed_paths - set(nodes)
    pending = list(changed_paths)
    while pending:
        path = pending.pop()
        node = nodes.get(path)
        if node is None:
            continue
        impacted_risk_classes.update(node.risk_classes)
        for dependent in node.dependents:
            if dependent not in impacted_paths:
                impacted_paths.add(dependent)
                pending.append(dependent)
    global_risk_paths = {
        path
        for path in changed_paths
        if any(
            matches_path_pattern(pattern, path)
            for pattern in input.dependency_graph.global_risk_paths
        )
    }
    return _ImpactFacts(
        impacted_paths=frozenset(impacted_paths),
        impacted_risk_classes=frozenset(impacted_risk_classes),
        unknown_paths=frozenset(unknown_paths),
        global_risk_paths=frozenset(global_risk_paths),
    )


def _omission_is_valid(
    input: PlanningInput,
    policy: PlanningPolicy,
    omitted: OmittedObligation,
    impact: _ImpactFacts,
    *,
    obligations: Mapping[str, ValidationObligation],
    repo_epoch_hash: str,
) -> bool:
    obligation = obligations.get(omitted.obligation_id)
    if (
        obligation is None
        or not obligation.omit_allowed
        or omitted.required_witness_ids != obligation.required_witness_ids
    ):
        return False
    proof = omitted.proof
    if (
        proof.obligation_id != obligation.obligation_id
        or proof.rule_id != obligation.obligation_id + ":no-impact"
        or proof.repo_epoch_hash != repo_epoch_hash
        or proof.diff_hash != input.diff.diff_hash
        or proof.policy_hash != policy.policy_hash
        or proof.graph_hash != input.dependency_graph.graph_hash
        or proof.catalog_hash != policy.catalog_hash
        or proof.predicates != OmissionPredicates()
        or proof.assumptions != _OMISSION_ASSUMPTIONS
        or proof.invalidates_when != _OMISSION_INVALIDATORS
    ):
        return False
    if obligation.responsibility_risk_classes and (
        set(obligation.responsibility_risk_classes) & impact.impacted_risk_classes
    ):
        return False
    return not any(
        matches_path_pattern(pattern, path)
        for pattern in obligation.responsibility_paths
        for path in impact.impacted_paths
    )


def _omissions_are_valid(
    input: PlanningInput,
    policy: PlanningPolicy,
    omitted_obligations: tuple[OmittedObligation, ...],
    impact: _ImpactFacts,
) -> bool:
    obligations = {item.obligation_id: item for item in policy.catalog.obligations}
    repo_epoch_hash = hash_object(input.repo_epoch.to_identity_mapping())
    return all(
        _omission_is_valid(
            input,
            policy,
            omitted,
            impact,
            obligations=obligations,
            repo_epoch_hash=repo_epoch_hash,
        )
        for omitted in omitted_obligations
    )
