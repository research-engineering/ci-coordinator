from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.reconciliation.contract import ReconciliationContract
from ci_coordinator.reconciliation.findings import (
    ReconciliationFinding,
    ReconciliationFindingKind,
    ReconciliationResult,
    ReconciliationState,
)
from ci_coordinator.reconciliation.observation import (
    OmittedSignal,
    SignalConclusion,
    SignalObservation,
)
from ci_coordinator.reconciliation.snapshot import ReconciliationSnapshot


@dataclass(frozen=True, slots=True)
class ReconciliationInput:
    snapshot: ReconciliationSnapshot

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ReconciliationSnapshot:
            raise TypeError("reconciliation input requires an exact snapshot")

    @property
    def subject_id(self) -> str:
        return self.snapshot.subject.subject_id

    @property
    def contract(self) -> ReconciliationContract:
        return self.snapshot.contract

    @property
    def provider_signals(self) -> tuple[ProviderSignal, ...]:
        return self.contract.provider_signals

    @property
    def omitted_signals(self) -> tuple[OmittedSignal, ...]:
        return self.contract.omitted_signals

    @property
    def observations(self) -> tuple[SignalObservation, ...]:
        return self.snapshot.observations


@dataclass(frozen=True, slots=True)
class _SignalAssessment:
    state: ReconciliationState
    findings: tuple[ReconciliationFinding, ...]


def classify_observed_state(reconciliation: ReconciliationInput) -> ReconciliationResult:
    findings = _omitted_execution_findings(reconciliation)
    assessments = tuple(
        _classify_expected_signal(signal, reconciliation.observations)
        for signal in reconciliation.provider_signals
    )
    findings += tuple(finding for assessment in assessments for finding in assessment.findings)

    if not reconciliation.provider_signals:
        findings += (
            ReconciliationFinding(
                kind="missing_expected_contract",
                signal_id=None,
                observation_ids=(),
                message="reconciliation has no expected signal contract",
            ),
        )

    state = _result_state(findings, assessments)
    return ReconciliationResult(
        subject_id=reconciliation.subject_id,
        state=state,
        findings=findings,
    )


def _omitted_execution_findings(
    reconciliation: ReconciliationInput,
) -> tuple[ReconciliationFinding, ...]:
    findings: list[ReconciliationFinding] = []
    for signal in reconciliation.omitted_signals:
        matches = tuple(
            observation
            for observation in reconciliation.observations
            if observation.signal_id == signal.signal_id
        )
        if matches:
            findings.append(
                ReconciliationFinding(
                    kind="omitted_execution",
                    signal_id=signal.signal_id,
                    observation_ids=tuple(observation.observation_id for observation in matches),
                    message=f"omitted signal {signal.name} was observed",
                )
            )
    return tuple(findings)


def _classify_expected_signal(
    signal: ProviderSignal,
    observations: tuple[SignalObservation, ...],
) -> _SignalAssessment:
    matches = tuple(
        observation for observation in observations if observation.signal_id == signal.signal_id
    )
    if not matches:
        return _assessment(
            "pending",
            "missing_expected_signal",
            signal,
            (),
            f"expected signal {signal.job_name} was not observed",
        )

    provider_job_ids = frozenset(observation.provider_job_id for observation in matches)
    if len(provider_job_ids) > 1:
        return _assessment(
            "failure",
            "ambiguous_provider_signal",
            signal,
            tuple(observation.observation_id for observation in matches),
            f"expected signal {signal.job_name} resolved to multiple provider jobs",
        )

    completed = tuple(observation for observation in matches if observation.status == "completed")
    conclusions = frozenset(observation.conclusion for observation in completed)
    if len(conclusions) > 1:
        return _assessment(
            "conflict",
            "contradictory_observation",
            signal,
            tuple(observation.observation_id for observation in completed),
            f"expected signal {signal.job_name} has contradictory terminal observations",
        )

    if not completed:
        return _assessment(
            "pending",
            "incomplete_expected_signal",
            signal,
            tuple(observation.observation_id for observation in matches),
            f"expected signal {signal.job_name} is incomplete",
        )

    conclusion = completed[0].conclusion
    if conclusion == "success":
        return _SignalAssessment(state="success", findings=())
    return _failed_conclusion_assessment(signal, completed, conclusion)


def _failed_conclusion_assessment(
    signal: ProviderSignal,
    completed: tuple[SignalObservation, ...],
    conclusion: SignalConclusion | None,
) -> _SignalAssessment:
    finding_kind: Literal[
        "skipped_without_proof", "neutral_without_policy", "failed_expected_signal"
    ]
    if conclusion == "skipped":
        finding_kind = "skipped_without_proof"
        message = f"expected signal {signal.job_name} was skipped without proof"
    elif conclusion == "neutral":
        finding_kind = "neutral_without_policy"
        message = f"expected signal {signal.job_name} was neutral without policy"
    else:
        finding_kind = "failed_expected_signal"
        message = f"expected signal {signal.job_name} concluded {conclusion}"
    return _assessment(
        "failure",
        finding_kind,
        signal,
        tuple(observation.observation_id for observation in completed),
        message,
    )


def _assessment(
    state: ReconciliationState,
    kind: ReconciliationFindingKind,
    signal: ProviderSignal,
    observation_ids: tuple[str, ...],
    message: str,
) -> _SignalAssessment:
    return _SignalAssessment(
        state=state,
        findings=(
            ReconciliationFinding(
                kind=kind,
                signal_id=signal.signal_id,
                observation_ids=observation_ids,
                message=message,
            ),
        ),
    )


def _result_state(
    findings: tuple[ReconciliationFinding, ...],
    assessments: tuple[_SignalAssessment, ...],
) -> ReconciliationState:
    finding_kinds = {finding.kind for finding in findings}
    if {"omitted_execution", "contradictory_observation"} & finding_kinds:
        return "conflict"
    if "missing_expected_contract" in finding_kinds or any(
        assessment.state == "failure" for assessment in assessments
    ):
        return "failure"
    if any(assessment.state == "pending" for assessment in assessments):
        return "pending"
    return "success"
