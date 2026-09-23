from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ci_coordinator.ci_economics.model import (
    AttemptIdentity,
    AttemptMeasurements,
    AttemptSnapshot,
    ProviderAttemptSnapshot,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource


@dataclass(frozen=True, slots=True)
class IndependentAttemptSnapshot:
    source: ProviderRunCollectionSource
    evidence: ProviderAttemptSnapshot
    recorded_at: datetime
    retain_until: datetime

    def __post_init__(self) -> None:
        if type(self.source) is not ProviderRunCollectionSource:
            raise TypeError("independent snapshot requires exact provider provenance")
        if type(self.evidence) is not ProviderAttemptSnapshot:
            raise TypeError("independent snapshot requires exact provider evidence")
        if (
            self.source.attempt != self.evidence.attempt
            or self.source.source_id != self.evidence.subject_id
        ):
            raise ValueError("independent snapshot differs from its source identity")
        for name, value in (("recorded_at", self.recorded_at), ("retain_until", self.retain_until)):
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("independent snapshot requires aware retention instants")
            object.__setattr__(self, name, value.astimezone(UTC))
        if not self.source.run_created_at <= self.recorded_at < self.retain_until:
            raise ValueError("independent snapshot retention differs from source time")


@dataclass(frozen=True, slots=True)
class RecordedAttemptEconomics:
    snapshot: AttemptSnapshot | IndependentAttemptSnapshot
    measurements: AttemptMeasurements

    def __post_init__(self) -> None:
        if type(self.snapshot) not in {AttemptSnapshot, IndependentAttemptSnapshot}:
            raise TypeError("recorded economics requires exact retained evidence")
        if type(self.measurements) is not AttemptMeasurements:
            raise TypeError("recorded economics requires exact measurements")
        evidence = (
            self.snapshot.evidence
            if isinstance(self.snapshot, IndependentAttemptSnapshot)
            else self.snapshot
        )
        if self.measurements.queue.total_job_count != len(evidence.jobs):
            raise ValueError("recorded measurement cardinality differs from its snapshot")

    @property
    def attempt(self) -> AttemptIdentity:
        return (
            self.snapshot.source.attempt
            if isinstance(self.snapshot, IndependentAttemptSnapshot)
            else self.snapshot.attempt
        )
