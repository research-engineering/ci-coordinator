"""Pure deterministic validation-obligation planning."""

from ci_coordinator.planning_core.impact import ImpactAnalysis, analyze_impact
from ci_coordinator.planning_core.model import (
    DeterministicPlan,
    OmissionProof,
    OmittedObligation,
    PlanEvidence,
    PlanFallback,
    PlanningRejected,
    PlanningResult,
    SelectedObligation,
    SelectedWitness,
    validate_witness_closure,
)
from ci_coordinator.planning_core.omission_proof import NotOmittable, build_omission_proof
from ci_coordinator.planning_core.planner import (
    close_selected_witnesses,
    full_ci_fallback_plan,
    plan,
)
from ci_coordinator.planning_core.policy import (
    AgentAdvicePolicy,
    PlanningPolicy,
    planner_admission_reasons,
    planner_rejection_reasons,
)
from ci_coordinator.planning_core.rollback_coverage import ConservativeEpochCoverageComparator
from ci_coordinator.validation_contract import ValidationDepth, depth_rank, max_depth

__all__ = [
    "AgentAdvicePolicy",
    "ConservativeEpochCoverageComparator",
    "DeterministicPlan",
    "ImpactAnalysis",
    "NotOmittable",
    "OmissionProof",
    "OmittedObligation",
    "PlanEvidence",
    "PlanFallback",
    "PlanningPolicy",
    "PlanningRejected",
    "PlanningResult",
    "SelectedObligation",
    "SelectedWitness",
    "ValidationDepth",
    "analyze_impact",
    "build_omission_proof",
    "close_selected_witnesses",
    "depth_rank",
    "full_ci_fallback_plan",
    "max_depth",
    "plan",
    "planner_admission_reasons",
    "planner_rejection_reasons",
    "validate_witness_closure",
]
