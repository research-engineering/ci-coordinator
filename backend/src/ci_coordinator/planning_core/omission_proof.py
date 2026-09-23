from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.planning_core.impact import ImpactAnalysis
from ci_coordinator.planning_core.model import OmissionPredicates, OmissionProof
from ci_coordinator.planning_core.policy import PlanningPolicy
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.repo_context.freshness import matches_path_pattern
from ci_coordinator.validation_contract import ValidationObligation


@dataclass(frozen=True, slots=True)
class NotOmittable:
    reason: str


def build_omission_proof(
    input: PlanningInput,
    policy: PlanningPolicy,
    obligation: ValidationObligation,
    impact: ImpactAnalysis,
) -> OmissionProof | NotOmittable:
    if not obligation.omit_allowed:
        return NotOmittable("policy does not allow omission")
    if impact.global_risk_paths:
        return NotOmittable("global risk paths changed: " + ", ".join(impact.global_risk_paths))
    impacted_path = next(
        (
            path
            for path in impact.impacted_paths
            if any(
                matches_path_pattern(pattern, path) for pattern in obligation.responsibility_paths
            )
        ),
        None,
    )
    if impacted_path is not None:
        return NotOmittable("impact intersects responsibility path " + impacted_path)
    impact_risks = set(impact.impacted_risk_classes)
    impacted_risk_class = next(
        (
            risk_class
            for risk_class in obligation.responsibility_risk_classes
            if risk_class in impact_risks
        ),
        None,
    )
    if impacted_risk_class is not None:
        return NotOmittable("impact intersects responsibility risk class " + impacted_risk_class)
    return OmissionProof(
        obligation_id=obligation.obligation_id,
        rule_id=obligation.obligation_id + ":no-impact",
        repo_epoch_hash=hash_object(input.repo_epoch.to_identity_mapping()),
        diff_hash=input.diff.diff_hash,
        policy_hash=policy.policy_hash,
        graph_hash=input.dependency_graph.graph_hash,
        catalog_hash=policy.catalog_hash,
        predicates=OmissionPredicates(),
        assumptions=tuple(
            sorted(
                {
                    "dependency graph includes all known dependents for changed paths",
                    "validation catalog is closed over obligation and witness identities",
                },
                key=utf16_sort_key,
            )
        ),
        invalidates_when=tuple(
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
        ),
    )
