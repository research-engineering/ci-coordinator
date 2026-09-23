"""Exact governance-state comparison algebra."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

type GovernanceChangedCoordinate = Literal[
    "api_version",
    "repository.owner_id",
    "repository.owner",
    "repository.name",
    "repository.full_name",
    "repository.default_branch",
    "rules",
]
type GovernanceComparisonRelation = Literal["matches", "differs"]

GOVERNANCE_CHANGED_COORDINATES: tuple[GovernanceChangedCoordinate, ...] = (
    "api_version",
    "repository.owner_id",
    "repository.owner",
    "repository.name",
    "repository.full_name",
    "repository.default_branch",
    "rules",
)

_COORDINATE_ORDER = {
    coordinate: index for index, coordinate in enumerate(GOVERNANCE_CHANGED_COORDINATES)
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class GovernanceStateComparison:
    relation: GovernanceComparisonRelation
    baseline_state_digest: str
    current_state_digest: str
    changed_coordinates: tuple[GovernanceChangedCoordinate, ...]
    added_rule_count: int
    removed_rule_count: int

    def __post_init__(self) -> None:
        if self.relation not in {"matches", "differs"}:
            raise ValueError("governance comparison relation is invalid")
        _require_digest(self.baseline_state_digest, "baseline")
        _require_digest(self.current_state_digest, "current")
        if type(self.changed_coordinates) is not tuple or any(
            coordinate not in _COORDINATE_ORDER for coordinate in self.changed_coordinates
        ):
            raise ValueError("governance changed coordinates are invalid")
        order = tuple(_COORDINATE_ORDER[value] for value in self.changed_coordinates)
        if order != tuple(sorted(set(order))):
            raise ValueError("governance changed coordinates must be unique canonical order")
        if (
            type(self.added_rule_count) is not int
            or type(self.removed_rule_count) is not int
            or self.added_rule_count < 0
            or self.removed_rule_count < 0
            or self.added_rule_count > 1_000
            or self.removed_rule_count > 1_000
        ):
            raise ValueError("governance rule delta counts are outside their state bound")
        rules_changed = "rules" in self.changed_coordinates
        if rules_changed != (self.added_rule_count > 0 or self.removed_rule_count > 0):
            raise ValueError("governance rule delta contradicts changed coordinates")
        equal_projection = (
            not self.changed_coordinates and self.baseline_state_digest == self.current_state_digest
        )
        relation_is_valid = (
            equal_projection if self.relation == "matches" else bool(self.changed_coordinates)
        )
        if not relation_is_valid:
            raise ValueError("governance comparison relation contradicts its exact projection")


def _require_digest(value: object, name: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} governance state digest is invalid")
