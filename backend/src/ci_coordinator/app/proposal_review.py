"""Authorization-first workflow-proposal review orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.kernel import Clock
from ci_coordinator.proposal_review import (
    ProposalReviewAttestationConflict,
    ProposalReviewBaselineConflict,
    ProposalReviewCommand,
    ProposalReviewCreated,
    ProposalReviewDraft,
    ProposalReviewDuplicate,
    ProposalReviewEpochConflict,
    ProposalReviewOperationConflict,
    ProposalReviewRecord,
    ProposalReviewStore,
    ProposalReviewStoreUnavailable,
    RepositoryAttestationEvidence,
    SemanticDiffLimitExceeded,
    compare_policy_drafts,
    prepare_proposal_review,
)
from ci_coordinator.workflow_discovery import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryUnavailable,
    WorkflowDiscoveryUseCase,
)

type ProposalReviewState = Literal[
    "accepted",
    "duplicate",
    "forbidden",
    "blocked",
    "stale",
    "diff_limit",
    "baseline_conflict",
    "operation_conflict",
    "epoch_conflict",
    "attestation_conflict",
    "overloaded",
    "unavailable",
]
_PROPOSAL_REVIEW_STATES = frozenset(
    {
        "accepted",
        "duplicate",
        "forbidden",
        "blocked",
        "stale",
        "diff_limit",
        "baseline_conflict",
        "operation_conflict",
        "epoch_conflict",
        "attestation_conflict",
        "overloaded",
        "unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class ProposalReviewOutcome:
    state: ProposalReviewState
    record: ProposalReviewRecord | None = None
    epoch_created: bool | None = None

    def __post_init__(self) -> None:
        if self.state not in _PROPOSAL_REVIEW_STATES:
            raise ValueError("proposal review outcome state is invalid")
        if self.record is not None and type(self.record) is not ProposalReviewRecord:
            raise TypeError("proposal review outcome record must be exact")
        if self.epoch_created is not None and type(self.epoch_created) is not bool:
            raise TypeError("proposal review outcome epoch-created fact must be boolean")
        accepted = self.state in {"accepted", "duplicate"}
        if accepted != (self.record is not None):
            raise ValueError("accepted proposal review outcome requires a retained record")
        if (self.state == "accepted") != (self.epoch_created is not None):
            raise ValueError("only a newly accepted review reports epoch creation")


class ProposalReviewAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class ProposalReviewService:
    def __init__(
        self,
        *,
        authorizer: ProposalReviewAuthorizer,
        discovery: WorkflowDiscoveryUseCase,
        store: ProposalReviewStore,
        clock: Clock,
    ) -> None:
        self._authorizer = authorizer
        self._discovery = discovery
        self._store = store
        self._clock = clock

    async def accept(
        self,
        command: ProposalReviewCommand,
        *,
        attestation: RepositoryAttestationEvidence,
    ) -> ProposalReviewOutcome:
        if type(command) is not ProposalReviewCommand:
            raise TypeError("proposal review service requires an exact command")
        if type(attestation) is not RepositoryAttestationEvidence:
            raise TypeError("proposal review service requires exact repository attestation")
        if not await self._authorizer.allows_scope(actor=command.actor, scope=command.scope):
            return ProposalReviewOutcome("forbidden")
        try:
            replay = await self._store.resolve_operation(command)
            if isinstance(replay, ProposalReviewDuplicate):
                return ProposalReviewOutcome("duplicate", record=replay.record)
            if isinstance(replay, ProposalReviewOperationConflict):
                return ProposalReviewOutcome("operation_conflict")
            active = await self._store.load_active(command.scope)
        except ProposalReviewStoreUnavailable:
            return ProposalReviewOutcome("unavailable")
        if (None if active is None else active.active) != command.expected_active:
            return ProposalReviewOutcome("baseline_conflict")

        discovered = await self._discovery(
            actor=command.actor,
            scope=command.scope,
            revision=None,
        )
        if isinstance(discovered, WorkflowDiscoveryForbidden):
            return ProposalReviewOutcome("forbidden")
        if isinstance(discovered, WorkflowDiscoveryUnavailable):
            return ProposalReviewOutcome(
                "overloaded" if discovered.reason == "overloaded" else "unavailable"
            )
        if not isinstance(discovered, WorkflowDiscoveryCompleted):
            return ProposalReviewOutcome("unavailable")
        report = discovered.report
        proposal = discovered.proposal
        if report.repository.scope != command.scope:
            return ProposalReviewOutcome("unavailable")
        if proposal.state != "reviewable" or type(proposal.admission) is not ValidatedEpochDraft:
            return ProposalReviewOutcome("blocked")
        binding = attestation.transaction.binding
        if (
            proposal.manifest_id != command.expected_manifest_id
            or report.revision != binding.revision
            or proposal.proposal_digest != binding.proposal_digest
        ):
            return ProposalReviewOutcome("stale")

        try:
            semantic_diff = compare_policy_drafts(
                None if active is None else active.draft,
                proposal.admission,
            )
        except SemanticDiffLimitExceeded:
            return ProposalReviewOutcome("diff_limit")
        except (TypeError, ValueError):
            return ProposalReviewOutcome("unavailable")
        review = ProposalReviewDraft(
            command=command,
            provider_revision=report.revision,
            inventory_digest=report.inventory_digest,
            proposal_digest=proposal.proposal_digest,
            target=proposal.admission,
            semantic_diff=semantic_diff,
        )
        prepared = prepare_proposal_review(
            review,
            attestation=attestation,
            occurred_at=_audit_timestamp(self._clock.now()),
        )
        try:
            result = await self._store.accept(prepared)
        except ProposalReviewStoreUnavailable:
            return ProposalReviewOutcome("unavailable")
        if isinstance(result, ProposalReviewCreated):
            return ProposalReviewOutcome(
                "accepted",
                record=result.record,
                epoch_created=result.epoch_created,
            )
        if isinstance(result, ProposalReviewDuplicate):
            return ProposalReviewOutcome("duplicate", record=result.record)
        if isinstance(result, ProposalReviewBaselineConflict):
            return ProposalReviewOutcome("baseline_conflict")
        if isinstance(result, ProposalReviewOperationConflict):
            return ProposalReviewOutcome("operation_conflict")
        if isinstance(result, ProposalReviewEpochConflict):
            return ProposalReviewOutcome("epoch_conflict")
        if isinstance(result, ProposalReviewAttestationConflict):
            return ProposalReviewOutcome("attestation_conflict")
        raise RuntimeError("proposal review result algebra is incomplete")


def _audit_timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("proposal review clock must return a timezone-aware instant")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
