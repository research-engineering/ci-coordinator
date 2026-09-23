from __future__ import annotations

from typing import Protocol

from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.reconciliation.check_summary import CheckSummary
from ci_coordinator.reconciliation.contract import ReconciliationContract
from ci_coordinator.reconciliation.convergence import (
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationConvergenceState,
)
from ci_coordinator.reconciliation.findings import ReconciliationResult
from ci_coordinator.reconciliation.observation import SignalObservation
from ci_coordinator.reconciliation.poll_outcome import ReconciliationPollOutcome
from ci_coordinator.reconciliation.snapshot import (
    ObservationAppend,
    ReconciliationSnapshot,
    ResultRecord,
    SubjectRegistration,
)
from ci_coordinator.reconciliation.subject import ReconciliationSubject


class ReconciliationPollUnavailable(RuntimeError):
    """An expected, redacted provider observation failure that permits retry."""


class ReconciliationPersistence(Protocol):
    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
    ) -> SubjectRegistration: ...

    async def load_snapshot(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationSnapshot | ReconciliationClaimLost: ...

    async def append_observation(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        observation: SignalObservation,
    ) -> ObservationAppend | ReconciliationClaimLost: ...

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> ResultRecord | ReconciliationClaimLost: ...

    async def defer_claim(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> ReconciliationConvergenceState | ReconciliationClaimLost: ...


class ReconciliationPublisher(Protocol):
    async def publish(self, summary: CheckSummary, *, revision: int) -> None: ...


class ReconciliationPoller(Protocol):
    async def poll(
        self,
        subject: ReconciliationSubject,
        provider_signals: tuple[ProviderSignal, ...],
    ) -> ReconciliationPollOutcome: ...
