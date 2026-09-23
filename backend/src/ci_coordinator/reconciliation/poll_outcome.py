"""Typed semantic outcomes from one exact-subject provider poll."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.reconciliation.findings import ReconciliationFinding, ReconciliationResult
from ci_coordinator.reconciliation.observation import SignalObservation


@dataclass(frozen=True, slots=True)
class ProviderSignalAmbiguity:
    subject_id: str
    signal: ProviderSignal

    def __post_init__(self) -> None:
        if (
            type(self.subject_id) is not str
            or len(self.subject_id) != 64
            or any(character not in "0123456789abcdef" for character in self.subject_id)
        ):
            raise ValueError("provider signal ambiguity requires a subject digest")
        if type(self.signal) is not ProviderSignal:
            raise TypeError("provider signal ambiguity requires an exact signal")


type ReconciliationPollOutcome = tuple[SignalObservation, ...] | ProviderSignalAmbiguity


def provider_signal_ambiguity_failure(
    ambiguity: ProviderSignalAmbiguity,
) -> ReconciliationResult:
    if type(ambiguity) is not ProviderSignalAmbiguity:
        raise TypeError("provider signal ambiguity failure requires an exact outcome")
    return ReconciliationResult(
        subject_id=ambiguity.subject_id,
        state="failure",
        findings=(
            ReconciliationFinding(
                kind="ambiguous_provider_signal",
                signal_id=ambiguity.signal.signal_id,
                observation_ids=(),
                message="more than one provider job matched the expected derived signal",
            ),
        ),
    )
