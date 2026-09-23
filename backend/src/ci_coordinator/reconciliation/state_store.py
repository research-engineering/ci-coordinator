"""Pure duplicate, conflict, and CAS algebra for durable reconciliation state."""

from __future__ import annotations

from ci_coordinator.kernel import is_safe_json_integer
from ci_coordinator.reconciliation.contract import ReconciliationContract
from ci_coordinator.reconciliation.findings import ReconciliationResult
from ci_coordinator.reconciliation.observation import SignalObservation
from ci_coordinator.reconciliation.snapshot import (
    ObservationAppend,
    ObservationAppended,
    ObservationConflict,
    ObservationDuplicate,
    ObservationRevisionConflict,
    ReconciliationSnapshot,
    ResultConflict,
    ResultDuplicate,
    ResultRecord,
    ResultRecorded,
    ResultRevisionConflict,
    SubjectRegistered,
    SubjectRegistration,
    SubjectRegistrationConflict,
    SubjectRegistrationDuplicate,
)
from ci_coordinator.reconciliation.subject import ReconciliationSubject


def register_subject(
    existing: ReconciliationSnapshot | None,
    subject: ReconciliationSubject,
    contract: ReconciliationContract,
) -> SubjectRegistration:
    """Create one immutable contract or classify an exact replay deterministically."""
    _require_subject(subject)
    _require_contract(contract)
    attempted = ReconciliationSnapshot(subject, contract, revision=0, observations=())
    if existing is None:
        return SubjectRegistered(attempted)
    _require_snapshot(existing)
    if existing.subject != subject:
        raise ValueError("existing reconciliation snapshot belongs to another subject")
    if existing.contract == contract:
        return SubjectRegistrationDuplicate(existing)
    return SubjectRegistrationConflict(existing)


def append_observation(
    snapshot: ReconciliationSnapshot,
    expected_revision: int,
    observation: SignalObservation,
) -> ObservationAppend:
    """Apply one new source observation only at the exact snapshot revision."""
    _require_snapshot(snapshot)
    _require_revision(expected_revision)
    if type(observation) is not SignalObservation:
        raise TypeError("reconciliation observation must be exact")
    if observation.subject_id != snapshot.subject.subject_id:
        raise ValueError("reconciliation observation belongs to another subject")
    if observation.workflow_run_id != snapshot.subject.workflow_run_id:
        raise ValueError("reconciliation observation belongs to another workflow run")
    if observation.run_attempt != snapshot.subject.run_attempt:
        raise ValueError("reconciliation observation belongs to another run attempt")
    existing = next(
        (
            candidate
            for candidate in snapshot.observations
            if candidate.observation_id == observation.observation_id
        ),
        None,
    )
    if existing is not None:
        if existing == observation:
            return ObservationDuplicate(snapshot, existing)
        return ObservationConflict(snapshot, existing)
    if expected_revision != snapshot.revision:
        return ObservationRevisionConflict(snapshot)
    successor_revision = snapshot.revision + 1
    _require_revision(successor_revision)
    successor = ReconciliationSnapshot(
        subject=snapshot.subject,
        contract=snapshot.contract,
        revision=successor_revision,
        observations=(*snapshot.observations, observation),
    )
    return ObservationAppended(successor)


def record_result(
    snapshot: ReconciliationSnapshot,
    expected_revision: int,
    result: ReconciliationResult,
    existing: ReconciliationResult | None,
) -> ResultRecord:
    """Store a result for one exact revision without promoting stale state."""
    _require_snapshot(snapshot)
    _require_revision(expected_revision)
    if type(result) is not ReconciliationResult:
        raise TypeError("reconciliation result must be exact")
    if result.subject_id != snapshot.subject.subject_id:
        raise ValueError("reconciliation result belongs to another subject")
    if existing is not None:
        if type(existing) is not ReconciliationResult:
            raise TypeError("stored reconciliation result must be exact")
        if existing.subject_id != snapshot.subject.subject_id:
            raise ValueError("stored reconciliation result belongs to another subject")
        if existing == result:
            return ResultDuplicate(expected_revision, existing)
        return ResultConflict(expected_revision, existing)
    if expected_revision != snapshot.revision:
        return ResultRevisionConflict(snapshot)
    return ResultRecorded(expected_revision, result)


def _require_subject(subject: object) -> None:
    if type(subject) is not ReconciliationSubject:
        raise TypeError("reconciliation subject must be exact")


def _require_contract(contract: object) -> None:
    if type(contract) is not ReconciliationContract:
        raise TypeError("reconciliation contract must be exact")


def _require_snapshot(snapshot: object) -> None:
    if type(snapshot) is not ReconciliationSnapshot:
        raise TypeError("reconciliation snapshot must be exact")


def _require_revision(revision: object) -> None:
    if type(revision) is not int or revision < 0 or not is_safe_json_integer(revision):
        raise ValueError("reconciliation revision must be a non-negative JSON safe integer")
