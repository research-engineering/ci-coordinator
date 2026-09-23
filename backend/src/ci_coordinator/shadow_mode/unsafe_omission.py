from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.shadow_mode.comparator import (
    ShadowComparison,
    ShadowComparisonClassification,
)


@dataclass(frozen=True, slots=True)
class UnsafeOmissionFinding:
    surface: str
    reason: str

    @property
    def blocks_enforcement(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SafeObservation:
    surface: str

    @property
    def is_positive_evidence(self) -> bool:
        return True


type UnsafeOmissionClassification = UnsafeOmissionFinding | SafeObservation | None


def classify_unsafe_omission(comparison: ShadowComparison) -> UnsafeOmissionClassification:
    if comparison.classification is ShadowComparisonClassification.UNSAFE:
        return UnsafeOmissionFinding(surface=comparison.surface, reason=comparison.reason)
    if comparison.classification is ShadowComparisonClassification.SAFE:
        return SafeObservation(surface=comparison.surface)
    return None
