from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.planning_core import (
    SelectedObligation,
    SelectedWitness,
    depth_rank,
)


@dataclass(frozen=True, slots=True)
class CoverageComparison:
    relation: Literal["equal", "strictly_greater", "incomparable", "less"]
    reasons: tuple[str, ...]


def compare_coverage(
    baseline_obligations: tuple[SelectedObligation, ...],
    baseline_witnesses: tuple[SelectedWitness, ...],
    candidate_obligations: tuple[SelectedObligation, ...],
    candidate_witnesses: tuple[SelectedWitness, ...],
) -> CoverageComparison:
    baseline_obligations_by_id = {item.obligation_id: item for item in baseline_obligations}
    candidate_obligations_by_id = {item.obligation_id: item for item in candidate_obligations}
    baseline_witnesses_by_id = {item.witness_id: item for item in baseline_witnesses}
    candidate_witnesses_by_id = {item.witness_id: item for item in candidate_witnesses}
    reasons: list[str] = []
    strengthened = False

    for obligation_id, expected_obligation in baseline_obligations_by_id.items():
        candidate_obligation = candidate_obligations_by_id.get(obligation_id)
        if candidate_obligation is None:
            reasons.append("selected_obligation_removed:" + obligation_id)
            continue
        if candidate_obligation.required_witness_ids != expected_obligation.required_witness_ids:
            reasons.append("obligation_requirements_changed:" + obligation_id)
        if depth_rank(candidate_obligation.depth) < depth_rank(expected_obligation.depth):
            reasons.append("obligation_depth_lowered:" + obligation_id)
        elif depth_rank(candidate_obligation.depth) > depth_rank(expected_obligation.depth):
            strengthened = True
    if set(candidate_obligations_by_id) - set(baseline_obligations_by_id):
        strengthened = True

    for witness_id, expected_witness in baseline_witnesses_by_id.items():
        candidate_witness = candidate_witnesses_by_id.get(witness_id)
        if candidate_witness is None:
            reasons.append("selected_witness_removed:" + witness_id)
            continue
        if not set(expected_witness.required_by_obligation_ids).issubset(
            candidate_witness.required_by_obligation_ids
        ):
            reasons.append("witness_requirement_edge_removed:" + witness_id)
        if depth_rank(candidate_witness.depth) < depth_rank(expected_witness.depth):
            reasons.append("witness_depth_lowered:" + witness_id)
        elif depth_rank(candidate_witness.depth) > depth_rank(expected_witness.depth):
            strengthened = True
    if set(candidate_witnesses_by_id) - set(baseline_witnesses_by_id):
        strengthened = True

    ordered_reasons = tuple(sorted(reasons))
    if ordered_reasons:
        return CoverageComparison(
            "incomparable" if strengthened else "less",
            ordered_reasons,
        )
    return CoverageComparison("strictly_greater" if strengthened else "equal", ())
