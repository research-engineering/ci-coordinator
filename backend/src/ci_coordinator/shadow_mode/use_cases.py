from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ci_coordinator.shadow_mode.candidate import FullCiObservation, ShadowCandidate
from ci_coordinator.shadow_mode.comparator import (
    ShadowComparison,
    ShadowComparisonClassification,
    compare_full_ci,
)
from ci_coordinator.shadow_mode.rollout_evidence import ShadowEvidenceRecord


@dataclass(frozen=True, slots=True)
class ShadowEvidenceStored:
    record: ShadowEvidenceRecord


@dataclass(frozen=True, slots=True)
class ShadowEvidenceDuplicate:
    record: ShadowEvidenceRecord


@dataclass(frozen=True, slots=True)
class ShadowEvidenceConflict:
    existing: ShadowEvidenceRecord


type ShadowEvidenceWrite = ShadowEvidenceStored | ShadowEvidenceDuplicate | ShadowEvidenceConflict


class ShadowEvidenceConflictError(RuntimeError):
    pass


class ShadowOutcomeRecorder(Protocol):
    async def record(self, record: ShadowEvidenceRecord) -> ShadowEvidenceWrite: ...


class ShadowEvidencePersistence(ShadowOutcomeRecorder, Protocol):
    async def list_records(self, profile_id: str) -> tuple[ShadowEvidenceRecord, ...]: ...


async def record_and_compare(
    candidate: ShadowCandidate,
    observation: FullCiObservation | None,
    *,
    profile_id: str,
    observed_at: datetime,
    recorder: ShadowOutcomeRecorder,
) -> ShadowComparison:
    comparison = compare_full_ci(candidate, observation)
    if comparison.classification is ShadowComparisonClassification.UNKNOWN:
        return comparison
    outcome = await recorder.record(
        ShadowEvidenceRecord(
            profile_id=profile_id,
            observed_at=observed_at,
            comparison=comparison,
        )
    )
    if isinstance(outcome, ShadowEvidenceConflict):
        raise ShadowEvidenceConflictError("shadow evidence key conflicts with retained semantics")
    return comparison
