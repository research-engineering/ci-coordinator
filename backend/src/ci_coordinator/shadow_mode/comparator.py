from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ci_coordinator.shadow_mode.candidate import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    ShadowCandidate,
    replay_identity_mismatches,
)


class ShadowComparisonClassification(StrEnum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"
    REPLAY_MISMATCH = "replay_mismatch"


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    candidate: ShadowCandidate
    observation: FullCiObservation | None
    classification: ShadowComparisonClassification
    reason: str
    mismatched_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected = _canonical_comparison_fields(self.candidate, self.observation)
        actual = (self.classification, self.reason, self.mismatched_fields)
        if actual != expected:
            raise ValueError("shadow comparison does not match canonical classification")

    @property
    def surface(self) -> str:
        return self.candidate.surface

    @property
    def blocks_enforcement(self) -> bool:
        return self.classification is not ShadowComparisonClassification.SAFE

    @property
    def is_positive_evidence(self) -> bool:
        return self.classification is ShadowComparisonClassification.SAFE


def compare_full_ci(
    candidate: ShadowCandidate,
    observation: FullCiObservation | None,
) -> ShadowComparison:
    classification, reason, mismatched_fields = _canonical_comparison_fields(candidate, observation)
    return ShadowComparison(
        candidate=candidate,
        observation=observation,
        classification=classification,
        reason=reason,
        mismatched_fields=mismatched_fields,
    )


def _canonical_comparison_fields(
    candidate: ShadowCandidate,
    observation: FullCiObservation | None,
) -> tuple[ShadowComparisonClassification, str, tuple[str, ...]]:
    if type(candidate) is not ShadowCandidate:
        raise TypeError("shadow comparison requires an exact candidate")
    if observation is not None and type(observation) is not FullCiObservation:
        raise TypeError("shadow comparison requires an exact FullCI observation")
    if observation is None:
        return (
            ShadowComparisonClassification.UNKNOWN,
            "missing FullCI observation",
            (),
        )

    mismatched_fields = replay_identity_mismatches(candidate, observation)
    if mismatched_fields:
        return (
            ShadowComparisonClassification.REPLAY_MISMATCH,
            "FullCI observation does not match the candidate replay identity",
            mismatched_fields,
        )

    if candidate.unsafe_candidate or candidate.coverage_relation is CoverageRelation.OMITTED:
        return (
            ShadowComparisonClassification.UNSAFE,
            "candidate omits a required validation surface",
            (),
        )

    if candidate.actual_full_ci_result is not FullCiResult.PASSED:
        return (
            ShadowComparisonClassification.UNKNOWN,
            "FullCI result is not positive evidence",
            (),
        )

    if candidate.coverage_relation is CoverageRelation.UNKNOWN:
        return (
            ShadowComparisonClassification.UNKNOWN,
            "candidate coverage relation is unknown",
            (),
        )

    return (
        ShadowComparisonClassification.SAFE,
        "replay identity, FullCI result, and coverage relation agree",
        (),
    )
