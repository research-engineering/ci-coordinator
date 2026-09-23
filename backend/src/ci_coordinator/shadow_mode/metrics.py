from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ci_coordinator.shadow_mode.comparator import (
    ShadowComparison,
    ShadowComparisonClassification,
)


@dataclass(frozen=True, slots=True)
class ShadowMetrics:
    safe: int
    unsafe: int
    unknown: int
    replay_mismatch: int

    @property
    def total(self) -> int:
        return self.safe + self.unsafe + self.unknown + self.replay_mismatch


def derive_metrics(comparisons: Iterable[ShadowComparison]) -> ShadowMetrics:
    counts = {classification: 0 for classification in ShadowComparisonClassification}
    for comparison in comparisons:
        counts[comparison.classification] += 1
    return ShadowMetrics(
        safe=counts[ShadowComparisonClassification.SAFE],
        unsafe=counts[ShadowComparisonClassification.UNSAFE],
        unknown=counts[ShadowComparisonClassification.UNKNOWN],
        replay_mismatch=counts[ShadowComparisonClassification.REPLAY_MISMATCH],
    )
