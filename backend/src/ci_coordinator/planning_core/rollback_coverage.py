"""Coverage ordering for rollback between retained planning policies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.config_epochs.rollback import CoverageRelation
from ci_coordinator.planning_core.policy import PlanningPolicy
from ci_coordinator.validation_contract import ValidationObligation, depth_rank


@dataclass(frozen=True, slots=True)
class ConservativeEpochCoverageComparator:
    """Prove only coverage relations represented by the current planning algebra."""

    active: ValidatedEpochDraft
    target: ValidatedEpochDraft

    async def compare(
        self,
        *,
        scope: RepositoryScope,
        active_epoch_id: str,
        target_epoch_id: str,
    ) -> CoverageRelation:
        if (
            type(self.active) is not ValidatedEpochDraft
            or type(self.target) is not ValidatedEpochDraft
            or type(scope) is not RepositoryScope
            or self.active.scope != scope
            or self.target.scope != scope
            or self.active.epoch_id != active_epoch_id
            or self.target.epoch_id != target_epoch_id
        ):
            return "unknown"
        try:
            return _compare_retained_epoch_coverage(self.active, self.target)
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return "unknown"


def _compare_retained_epoch_coverage(
    active: ValidatedEpochDraft,
    target: ValidatedEpochDraft,
) -> CoverageRelation:
    if active.compiled_policy_bytes == target.compiled_policy_bytes:
        return "equal"

    active_outer, active_dynamic = _compiled_policy_parts(active.compiled_policy_bytes)
    target_outer, target_dynamic = _compiled_policy_parts(target.compiled_policy_bytes)
    if active_outer != target_outer or active_dynamic is None or target_dynamic is None:
        return "incomparable"

    active_projection = project_dynamic_ci_planning(active)
    target_projection = project_dynamic_ci_planning(target)
    if active_projection is None or target_projection is None:
        return "incomparable"
    if (
        active_projection.dependency_graph_source != target_projection.dependency_graph_source
        or active_projection.global_risk_paths != target_projection.global_risk_paths
        or active_projection.risk_classes != target_projection.risk_classes
        or active_projection.agent_advice != target_projection.agent_advice
        or active_projection.fallback_timeout_seconds != target_projection.fallback_timeout_seconds
    ):
        return "incomparable"

    active_policy = PlanningPolicy.from_projection(active_projection)
    target_policy = PlanningPolicy.from_projection(target_projection)
    if (
        active_policy.catalog.witnesses != target_policy.catalog.witnesses
        or active_policy.catalog.execution_profiles != target_policy.catalog.execution_profiles
        or tuple(item.obligation_id for item in active_policy.catalog.obligations)
        != tuple(item.obligation_id for item in target_policy.catalog.obligations)
    ):
        return "incomparable"

    directions: list[int] = []
    for active_obligation, target_obligation in zip(
        active_policy.catalog.obligations,
        target_policy.catalog.obligations,
        strict=True,
    ):
        relation = _compare_obligation_coverage(active_obligation, target_obligation)
        if relation is None:
            return "incomparable"
        directions.extend(relation)
    return _collapse_directions(directions)


def _compare_obligation_coverage(
    active: ValidationObligation,
    target: ValidationObligation,
) -> tuple[int, ...] | None:
    if (
        active.obligation_id != target.obligation_id
        or active.required_witness_ids != target.required_witness_ids
    ):
        return None

    directions = [
        _order(depth_rank(active.default_depth), depth_rank(target.default_depth)),
        _order(depth_rank(active.full_depth), depth_rank(target.full_depth)),
        _order(int(active.omit_allowed), int(target.omit_allowed)) * -1,
    ]
    if active.omit_allowed and target.omit_allowed:
        path_direction = _set_order(
            active.responsibility_paths,
            target.responsibility_paths,
        )
        risk_direction = _set_order(
            active.responsibility_risk_classes,
            target.responsibility_risk_classes,
        )
        if path_direction is None or risk_direction is None:
            return None
        directions.extend((path_direction, risk_direction))
    return tuple(directions)


def _compiled_policy_parts(value: bytes) -> tuple[dict[str, object], object]:
    decoded: object = json.loads(value)
    if type(decoded) is not dict:
        raise ValueError("compiled policy must be an object")
    compiled = cast(dict[str, object], decoded)
    if "dynamicCi" not in compiled:
        raise ValueError("compiled policy is missing dynamic CI state")
    return (
        {key: item for key, item in compiled.items() if key != "dynamicCi"},
        compiled["dynamicCi"],
    )


def _set_order(active: tuple[str, ...], target: tuple[str, ...]) -> int | None:
    active_set = set(active)
    target_set = set(target)
    if active_set == target_set:
        return 0
    if active_set < target_set:
        return 1
    if target_set < active_set:
        return -1
    return None


def _order(active: int, target: int) -> int:
    return (target > active) - (target < active)


def _collapse_directions(directions: list[int]) -> CoverageRelation:
    greater = any(direction > 0 for direction in directions)
    less = any(direction < 0 for direction in directions)
    if greater and less:
        return "incomparable"
    if greater:
        return "greater"
    if less:
        return "less"
    return "equal"
