"""Application orchestration for one bounded reconciliation claim round."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from ci_coordinator.kernel import Clock
from ci_coordinator.reconciliation import (
    ConvergenceTerminalReason,
    ObservationAppended,
    ObservationDuplicate,
    ProviderSignalAmbiguity,
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationConvergencePolicy,
    ReconciliationInput,
    ReconciliationPersistence,
    ReconciliationPoller,
    ReconciliationPollUnavailable,
    ReconciliationResult,
    ResultDuplicate,
    ResultRecord,
    ResultRecorded,
    convergence_failure,
    provider_signal_ambiguity_failure,
    reconcile,
)
from ci_coordinator.shadow_mode import ShadowEvidenceRecord


class ReconciliationClaimSource(Protocol):
    async def claim_next(self) -> ReconciliationAttemptClaim | None: ...


class TerminalReconciliationPersistence(Protocol):
    async def record_result_with_evidence(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
        evidence: tuple[ShadowEvidenceRecord, ...],
    ) -> ResultRecord | ReconciliationClaimLost: ...


class ShadowEvidenceProjector(Protocol):
    def project(
        self,
        claim: ReconciliationAttemptClaim,
        result: ReconciliationResult,
    ) -> tuple[ShadowEvidenceRecord, ...]: ...


class ReconciliationRoundService:
    """Claim and reconcile at most one configured batch without overlapping replicas."""

    def __init__(
        self,
        *,
        source: ReconciliationClaimSource,
        persistence: ReconciliationPersistence,
        poller: ReconciliationPoller,
        clock: Clock,
        convergence_policy: ReconciliationConvergencePolicy,
        max_claims: int,
        terminal_persistence: TerminalReconciliationPersistence | None = None,
        shadow_projector: ShadowEvidenceProjector | None = None,
    ) -> None:
        if (terminal_persistence is None) != (shadow_projector is None):
            raise ValueError(
                "terminal persistence and shadow projector must be configured together"
            )
        if type(convergence_policy) is not ReconciliationConvergencePolicy:
            raise TypeError("reconciliation convergence policy must be exact")
        if type(max_claims) is not int or not 1 <= max_claims <= 1_000:
            raise ValueError("reconciliation claim bound must be between 1 and 1000")
        self._source = source
        self._persistence = persistence
        self._poller = poller
        self._clock = clock
        self._convergence_policy = convergence_policy
        self._max_claims = max_claims
        self._terminal_persistence = terminal_persistence
        self._shadow_projector = shadow_projector

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        for _ in range(self._max_claims):
            if abort_signal.is_set():
                return
            claim = await self._source.claim_next()
            if claim is None:
                return
            if abort_signal.is_set():
                return
            await self._reconcile(claim)

    async def _reconcile(self, claim: ReconciliationAttemptClaim) -> None:
        if claim.terminal_reason is not None:
            await self._record_terminal(
                claim,
                claim.revision,
                convergence_failure(claim, claim.terminal_reason),
            )
            return
        deadline = asyncio.timeout(self._convergence_policy.poll_timeout_seconds)
        try:
            async with deadline:
                poll_outcome = await self._poller.poll(
                    claim.subject,
                    claim.contract.provider_signals,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if not deadline.expired():
                raise
            await self._defer_or_fail(claim)
            return
        except ReconciliationPollUnavailable:
            await self._defer_or_fail(claim)
            return
        if isinstance(poll_outcome, ProviderSignalAmbiguity):
            if poll_outcome.subject_id != claim.subject.subject_id:
                raise ValueError("provider ambiguity belongs to another subject")
            result = (
                convergence_failure(claim, "deadline_exceeded")
                if self._clock.now() >= claim.deadline_at
                else provider_signal_ambiguity_failure(poll_outcome)
            )
            await self._record_terminal(claim, claim.revision, result)
            return
        observations = poll_outcome
        if type(observations) is not tuple:
            raise TypeError("reconciliation poller must return an exact tuple")

        snapshot = await self._persistence.load_snapshot(claim)
        if isinstance(snapshot, ReconciliationClaimLost):
            return
        for observation in observations:
            outcome = await self._persistence.append_observation(
                claim,
                snapshot.revision,
                observation,
            )
            if isinstance(outcome, ReconciliationClaimLost):
                return
            if isinstance(outcome, ObservationAppended | ObservationDuplicate):
                snapshot = outcome.snapshot
                continue
            return

        result = reconcile(ReconciliationInput(snapshot))
        now = self._clock.now()
        if now >= claim.deadline_at:
            await self._record_terminal(
                claim,
                snapshot.revision,
                convergence_failure(claim, "deadline_exceeded"),
            )
        elif result.state == "pending":
            if claim.attempt_count >= claim.max_attempts:
                await self._record_terminal(
                    claim,
                    snapshot.revision,
                    convergence_failure(claim, "attempts_exhausted"),
                )
            else:
                await self._persistence.defer_claim(claim)
        else:
            await self._record_terminal(claim, snapshot.revision, result)

    async def _defer_or_fail(self, claim: ReconciliationAttemptClaim) -> None:
        reason = _terminal_reason(claim, self._clock.now())
        if reason is None:
            await self._persistence.defer_claim(claim)
            return
        await self._record_terminal(
            claim,
            claim.revision,
            convergence_failure(claim, reason),
        )

    async def _record_terminal(
        self,
        claim: ReconciliationAttemptClaim,
        revision: int,
        result: ReconciliationResult,
    ) -> None:
        if self._terminal_persistence is None or self._shadow_projector is None:
            recorded = await self._persistence.record_result(claim, revision, result)
        else:
            recorded = await self._terminal_persistence.record_result_with_evidence(
                claim,
                revision,
                result,
                self._shadow_projector.project(claim, result),
            )
        if not isinstance(recorded, ResultRecorded | ResultDuplicate | ReconciliationClaimLost):
            return


def _terminal_reason(
    claim: ReconciliationAttemptClaim,
    now: datetime,
) -> ConvergenceTerminalReason | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("reconciliation decision time must be timezone-aware")
    return (
        "deadline_exceeded"
        if now >= claim.deadline_at
        else "attempts_exhausted"
        if claim.attempt_count >= claim.max_attempts
        else None
    )
