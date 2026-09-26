"""Evaluate precommitted, non-vacuous shadow rollout evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from ci_coordinator.kernel import hash_object, is_safe_json_integer
from ci_coordinator.shadow_mode.comparator import (
    ShadowComparison,
    ShadowComparisonClassification,
)

_ROLLOUT_EVIDENCE_PROFILE_SCHEMA_VERSION: Final = "ci-shadow-rollout-evidence-profile/v1"
_ROLLOUT_EVIDENCE_EVALUATOR_VERSION: Final = "ci-shadow-rollout-evidence-evaluator/v1"


class RolloutEvidenceState(StrEnum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    INSUFFICIENT_COUNT = "insufficient_count"
    INSUFFICIENT_DURATION = "insufficient_duration"
    STALE = "stale"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ShadowEvidenceKey:
    profile_id: str
    repository: str
    event: str
    surface: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.profile_id, "profile id"),
            (self.repository, "repository"),
            (self.event, "event"),
            (self.surface, "surface"),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"shadow evidence {field_name} must be non-empty text")


@dataclass(frozen=True, slots=True)
class SurfaceEvidenceRequirement:
    surface: str
    minimum_comparison_count: int
    minimum_observation_seconds: int
    maximum_evidence_age_seconds: int

    def __post_init__(self) -> None:
        if type(self.surface) is not str or not self.surface:
            raise ValueError("surface evidence requirement requires a non-empty surface")
        for value, name in (
            (self.minimum_comparison_count, "minimum comparison count"),
            (self.minimum_observation_seconds, "minimum observation seconds"),
            (self.maximum_evidence_age_seconds, "maximum evidence age seconds"),
        ):
            if type(value) is not int or value < 1 or not is_safe_json_integer(value):
                raise ValueError(f"{name} must be a positive JSON-safe integer")


@dataclass(frozen=True, slots=True)
class RolloutEvidenceProfile:
    requirements: tuple[SurfaceEvidenceRequirement, ...]
    profile_id: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.requirements) is not tuple or not self.requirements:
            raise ValueError("rollout evidence profile requires at least one surface")
        if any(
            type(requirement) is not SurfaceEvidenceRequirement for requirement in self.requirements
        ):
            raise TypeError("rollout evidence profile requirements must be exact requirements")
        surfaces = tuple(requirement.surface for requirement in self.requirements)
        if len(surfaces) != len(set(surfaces)):
            raise ValueError("rollout evidence profile must not repeat surfaces")
        canonical_requirements = tuple(
            sorted(self.requirements, key=lambda requirement: requirement.surface)
        )
        object.__setattr__(self, "requirements", canonical_requirements)
        object.__setattr__(
            self,
            "profile_id",
            hash_object(_profile_identity_mapping(canonical_requirements)),
        )


def _profile_identity_mapping(
    requirements: tuple[SurfaceEvidenceRequirement, ...],
) -> dict[str, object]:
    return {
        "schemaVersion": _ROLLOUT_EVIDENCE_PROFILE_SCHEMA_VERSION,
        "evaluatorVersion": _ROLLOUT_EVIDENCE_EVALUATOR_VERSION,
        "requirements": [
            {
                "surface": requirement.surface,
                "minimumComparisonCount": requirement.minimum_comparison_count,
                "minimumObservationSeconds": requirement.minimum_observation_seconds,
                "maximumEvidenceAgeSeconds": requirement.maximum_evidence_age_seconds,
            }
            for requirement in requirements
        ],
    }


@dataclass(frozen=True, slots=True)
class ShadowEvidenceRecord:
    profile_id: str
    observed_at: datetime
    comparison: ShadowComparison

    def __post_init__(self) -> None:
        if type(self.profile_id) is not str or not self.profile_id:
            raise ValueError("shadow evidence requires a non-empty profile id")
        if (
            type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("shadow evidence timestamp must be timezone-aware")
        if type(self.comparison) is not ShadowComparison:
            raise TypeError("shadow evidence requires an exact comparison")
        if self.comparison.classification is ShadowComparisonClassification.UNKNOWN:
            raise ValueError("unknown shadow comparison is not durable evidence")

    @property
    def key(self) -> ShadowEvidenceKey:
        candidate = self.comparison.candidate
        return ShadowEvidenceKey(
            profile_id=self.profile_id,
            repository=candidate.repo,
            event=candidate.event,
            surface=candidate.surface,
        )

    def has_same_semantics_as(self, other: ShadowEvidenceRecord) -> bool:
        if type(other) is not ShadowEvidenceRecord:
            raise TypeError("shadow evidence comparison requires an exact record")
        return self.key == other.key and self.comparison == other.comparison


@dataclass(frozen=True, slots=True)
class SurfaceEvidenceAssessment:
    surface: str
    state: RolloutEvidenceState
    safe_comparison_count: int
    blocked_comparison_count: int
    observed_duration_seconds: int


@dataclass(frozen=True, slots=True)
class RolloutEvidenceAssessment:
    profile_id: str
    satisfied: bool
    surfaces: tuple[SurfaceEvidenceAssessment, ...]


def evaluate_rollout_evidence(
    profile: RolloutEvidenceProfile,
    records: tuple[ShadowEvidenceRecord, ...],
    *,
    now: datetime,
) -> RolloutEvidenceAssessment:
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("rollout evidence evaluation time must be timezone-aware")
    if type(records) is not tuple or any(
        type(record) is not ShadowEvidenceRecord for record in records
    ):
        raise TypeError("shadow evidence records must be an exact tuple")
    keys = tuple(record.key for record in records)
    if len(keys) != len(set(keys)):
        raise ValueError("shadow evidence records must not repeat a durable key")

    assessments = tuple(
        _assess_surface(requirement, profile.profile_id, records, now)
        for requirement in profile.requirements
    )
    return RolloutEvidenceAssessment(
        profile_id=profile.profile_id,
        satisfied=all(
            assessment.state is RolloutEvidenceState.SATISFIED for assessment in assessments
        ),
        surfaces=assessments,
    )


def _assess_surface(
    requirement: SurfaceEvidenceRequirement,
    profile_id: str,
    records: tuple[ShadowEvidenceRecord, ...],
    now: datetime,
) -> SurfaceEvidenceAssessment:
    matching = tuple(
        record
        for record in records
        if record.profile_id == profile_id and record.comparison.surface == requirement.surface
    )
    safe = tuple(
        record
        for record in matching
        if record.comparison.classification is ShadowComparisonClassification.SAFE
    )
    blocked = tuple(record for record in matching if record not in safe)
    duration_seconds = _observed_duration_seconds(safe)
    state = _state_for(requirement, safe, blocked, duration_seconds, now)
    return SurfaceEvidenceAssessment(
        surface=requirement.surface,
        state=state,
        safe_comparison_count=len(safe),
        blocked_comparison_count=len(blocked),
        observed_duration_seconds=duration_seconds,
    )


def _state_for(
    requirement: SurfaceEvidenceRequirement,
    safe: tuple[ShadowEvidenceRecord, ...],
    blocked: tuple[ShadowEvidenceRecord, ...],
    duration_seconds: int,
    now: datetime,
) -> RolloutEvidenceState:
    if blocked:
        return RolloutEvidenceState.BLOCKED
    if not safe:
        return RolloutEvidenceState.MISSING
    newest = max(record.observed_at for record in safe)
    if newest > now or (now - newest).total_seconds() > requirement.maximum_evidence_age_seconds:
        return RolloutEvidenceState.STALE
    if len(safe) < requirement.minimum_comparison_count:
        return RolloutEvidenceState.INSUFFICIENT_COUNT
    if duration_seconds < requirement.minimum_observation_seconds:
        return RolloutEvidenceState.INSUFFICIENT_DURATION
    return RolloutEvidenceState.SATISFIED


def _observed_duration_seconds(records: tuple[ShadowEvidenceRecord, ...]) -> int:
    if not records:
        return 0
    timestamps = tuple(record.observed_at for record in records)
    return int((max(timestamps) - min(timestamps)).total_seconds())
