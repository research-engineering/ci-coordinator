"""Pure canonical-byte comparison of effective governance states."""

from __future__ import annotations

from ci_coordinator.governance_comparison.model import (
    GovernanceChangedCoordinate,
    GovernanceStateComparison,
)
from ci_coordinator.governance_observation import GovernanceState, encode_governance_state
from ci_coordinator.kernel import sha256_hex


def compare_governance_states(
    baseline: GovernanceState,
    current: GovernanceState,
) -> GovernanceStateComparison:
    if type(baseline) is not GovernanceState or type(current) is not GovernanceState:
        raise TypeError("governance comparison requires exact states")
    if baseline.repository.scope != current.repository.scope:
        raise ValueError("governance comparison cannot cross repository scope")

    baseline_bytes = encode_governance_state(baseline)
    current_bytes = encode_governance_state(current)
    baseline_rules = {rule.canonical_json for rule in baseline.rules}
    current_rules = {rule.canonical_json for rule in current.rules}
    changed: list[GovernanceChangedCoordinate] = []

    if baseline.api_version != current.api_version:
        changed.append("api_version")
    baseline_repository = baseline.repository
    current_repository = current.repository
    comparisons: tuple[tuple[GovernanceChangedCoordinate, object, object], ...] = (
        ("repository.owner_id", baseline_repository.owner_id, current_repository.owner_id),
        ("repository.owner", baseline_repository.owner, current_repository.owner),
        ("repository.name", baseline_repository.name, current_repository.name),
        ("repository.full_name", baseline_repository.full_name, current_repository.full_name),
        (
            "repository.default_branch",
            baseline_repository.default_branch,
            current_repository.default_branch,
        ),
    )
    for coordinate, left, right in comparisons:
        if left != right:
            changed.append(coordinate)
    added_rules = current_rules - baseline_rules
    removed_rules = baseline_rules - current_rules
    if added_rules or removed_rules:
        changed.append("rules")

    return GovernanceStateComparison(
        relation="matches" if baseline_bytes == current_bytes else "differs",
        baseline_state_digest=sha256_hex(baseline_bytes),
        current_state_digest=sha256_hex(current_bytes),
        changed_coordinates=tuple(changed),
        added_rule_count=len(added_rules),
        removed_rule_count=len(removed_rules),
    )
