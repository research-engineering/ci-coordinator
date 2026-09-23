"""Immutable reconciliation read model and replay-safe write outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.kernel import is_safe_json_integer
from ci_coordinator.reconciliation.contract import ReconciliationContract
from ci_coordinator.reconciliation.findings import ReconciliationResult
from ci_coordinator.reconciliation.observation import SignalObservation
from ci_coordinator.reconciliation.subject import ReconciliationSubject


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshot:
    subject: ReconciliationSubject
    contract: ReconciliationContract
    revision: int
    observations: tuple[SignalObservation, ...]

    def __post_init__(self) -> None:
        if type(self.subject) is not ReconciliationSubject:
            raise TypeError("reconciliation snapshot requires an exact subject")
        if type(self.contract) is not ReconciliationContract:
            raise TypeError("reconciliation snapshot requires an exact contract")
        if (
            type(self.revision) is not int
            or self.revision < 0
            or not is_safe_json_integer(self.revision)
        ):
            raise ValueError(
                "reconciliation snapshot revision must be a non-negative JSON safe integer"
            )
        if type(self.observations) is not tuple or any(
            type(observation) is not SignalObservation for observation in self.observations
        ):
            raise TypeError("reconciliation snapshot observations must be exact observations")
        observation_ids = tuple(observation.observation_id for observation in self.observations)
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("reconciliation snapshot observations must not repeat observation_id")
        if self.revision != len(self.observations):
            raise ValueError("reconciliation snapshot revision must equal its observation count")
        if any(
            observation.subject_id != self.subject.subject_id for observation in self.observations
        ):
            raise ValueError("reconciliation snapshot observations must belong to its subject")
        if any(
            observation.workflow_run_id != self.subject.workflow_run_id
            for observation in self.observations
        ):
            raise ValueError(
                "reconciliation snapshot observations must belong to its exact workflow run"
            )
        if any(
            observation.run_attempt != self.subject.run_attempt for observation in self.observations
        ):
            raise ValueError(
                "reconciliation snapshot observations must belong to its exact run attempt"
            )


@dataclass(frozen=True, slots=True)
class SubjectRegistered:
    snapshot: ReconciliationSnapshot


@dataclass(frozen=True, slots=True)
class SubjectRegistrationDuplicate:
    snapshot: ReconciliationSnapshot


@dataclass(frozen=True, slots=True)
class SubjectRegistrationConflict:
    existing: ReconciliationSnapshot


type SubjectRegistration = (
    SubjectRegistered | SubjectRegistrationDuplicate | SubjectRegistrationConflict
)


@dataclass(frozen=True, slots=True)
class ObservationAppended:
    snapshot: ReconciliationSnapshot


@dataclass(frozen=True, slots=True)
class ObservationDuplicate:
    snapshot: ReconciliationSnapshot
    existing: SignalObservation


@dataclass(frozen=True, slots=True)
class ObservationConflict:
    snapshot: ReconciliationSnapshot
    existing: SignalObservation


@dataclass(frozen=True, slots=True)
class ObservationRevisionConflict:
    snapshot: ReconciliationSnapshot


type ObservationAppend = (
    ObservationAppended | ObservationDuplicate | ObservationConflict | ObservationRevisionConflict
)


@dataclass(frozen=True, slots=True)
class ResultRecorded:
    revision: int
    result: ReconciliationResult


@dataclass(frozen=True, slots=True)
class ResultDuplicate:
    revision: int
    existing: ReconciliationResult


@dataclass(frozen=True, slots=True)
class ResultConflict:
    revision: int
    existing: ReconciliationResult


@dataclass(frozen=True, slots=True)
class ResultRevisionConflict:
    snapshot: ReconciliationSnapshot


type ResultRecord = ResultRecorded | ResultDuplicate | ResultConflict | ResultRevisionConflict
