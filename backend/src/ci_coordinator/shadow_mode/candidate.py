from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class CoverageRelation(StrEnum):
    COVERED = "covered"
    OMITTED = "omitted"
    UNKNOWN = "unknown"


class FullCiResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"


REPLAY_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "repo",
    "event",
    "base_sha",
    "head_sha",
    "config_epoch",
    "policy_hash",
    "diff_hash",
    "graph_hash",
    "baseline_plan",
    "candidate_plan",
)


@dataclass(frozen=True, slots=True)
class ShadowCandidate:
    repo: str
    event: str
    base_sha: str
    head_sha: str
    config_epoch: str
    policy_hash: str
    diff_hash: str
    graph_hash: str
    baseline_plan: str
    candidate_plan: str
    surface: str
    coverage_relation: CoverageRelation
    actual_full_ci_result: FullCiResult
    unsafe_candidate: bool = False

    def __post_init__(self) -> None:
        _validate_replay_identity(self)
        _require_nonempty_string(self.surface, field_name="surface")
        if not isinstance(self.coverage_relation, CoverageRelation):
            raise ValueError("coverage_relation must be a CoverageRelation")
        if not isinstance(self.actual_full_ci_result, FullCiResult):
            raise ValueError("actual_full_ci_result must be a FullCiResult")
        if type(self.unsafe_candidate) is not bool:
            raise ValueError("unsafe_candidate must be a bool")


@dataclass(frozen=True, slots=True)
class FullCiObservation:
    repo: str
    event: str
    base_sha: str
    head_sha: str
    config_epoch: str
    policy_hash: str
    diff_hash: str
    graph_hash: str
    baseline_plan: str
    candidate_plan: str
    actual_full_ci_result: FullCiResult

    def __post_init__(self) -> None:
        _validate_replay_identity(self)
        if not isinstance(self.actual_full_ci_result, FullCiResult):
            raise ValueError("actual_full_ci_result must be a FullCiResult")


def replay_identity_mismatches(
    candidate: ShadowCandidate,
    observation: FullCiObservation,
) -> tuple[str, ...]:
    mismatches = tuple(
        field_name
        for field_name in REPLAY_IDENTITY_FIELDS
        if getattr(candidate, field_name) != getattr(observation, field_name)
    )
    if candidate.actual_full_ci_result != observation.actual_full_ci_result:
        return (*mismatches, "actual_full_ci_result")
    return mismatches


def _validate_replay_identity(value: ShadowCandidate | FullCiObservation) -> None:
    for field_name in REPLAY_IDENTITY_FIELDS:
        _require_nonempty_string(getattr(value, field_name), field_name=field_name)


def _require_nonempty_string(value: object, *, field_name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
