"""Authorization-first review orchestration for one registered, retained config epoch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import Clock
from ci_coordinator.proposal_review import (
    ProposalReviewStoreUnavailable,
    RegisteredEpochAttestationEvidence,
    RegisteredEpochReviewAttestationConflict,
    RegisteredEpochReviewBaselineConflict,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewCreated,
    RegisteredEpochReviewDraft,
    RegisteredEpochReviewDuplicate,
    RegisteredEpochReviewOperationConflict,
    RegisteredEpochReviewRecord,
    RegisteredEpochReviewStore,
    RegisteredEpochSubject,
    RegisteredEpochWithoutReceipt,
    SemanticDiffLimitExceeded,
    compare_policy_drafts,
    prepare_registered_epoch_review,
)
from ci_coordinator.workflow_discovery import (
    DiscoveryReport,
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryUnavailable,
    WorkflowDiscoveryUseCase,
)

type RegisteredEpochReviewState = Literal[
    "accepted",
    "duplicate",
    "forbidden",
    "target_missing",
    "target_ineligible",
    "source_mismatch",
    "blocked",
    "stale",
    "diff_limit",
    "baseline_conflict",
    "operation_conflict",
    "attestation_conflict",
    "overloaded",
    "unavailable",
]
_REGISTERED_EPOCH_REVIEW_STATES: Final = frozenset(
    {
        "accepted",
        "duplicate",
        "forbidden",
        "target_missing",
        "target_ineligible",
        "source_mismatch",
        "blocked",
        "stale",
        "diff_limit",
        "baseline_conflict",
        "operation_conflict",
        "attestation_conflict",
        "overloaded",
        "unavailable",
    }
)
type RegisteredEpochTargetRefusal = Literal[
    "target_missing",
    "target_ineligible",
    "source_mismatch",
]
type CurrentInventoryRefusal = Literal["forbidden", "blocked", "overloaded", "unavailable"]


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewOutcome:
    state: RegisteredEpochReviewState
    record: RegisteredEpochReviewRecord | None = None

    def __post_init__(self) -> None:
        if self.state not in _REGISTERED_EPOCH_REVIEW_STATES:
            raise ValueError("registered-epoch review outcome state is invalid")
        if self.record is not None and type(self.record) is not RegisteredEpochReviewRecord:
            raise TypeError("registered-epoch review outcome record must be exact")
        if (self.state in {"accepted", "duplicate"}) != (self.record is not None):
            raise ValueError("accepted registered-epoch review outcome requires a record")


def admit_registered_epoch_target(
    lookup: object,
    command: RegisteredEpochReviewCommand,
) -> RegisteredEpochSubject | RegisteredEpochTargetRefusal:
    """Keep absent, receipt-less and source-mismatched targets distinct."""
    if lookup is None:
        return "target_missing"
    if type(lookup) is RegisteredEpochWithoutReceipt:
        return "target_ineligible"
    if type(lookup) is not RegisteredEpochSubject:
        raise TypeError("registered-epoch lookup result is not admitted")
    draft = lookup.draft
    if draft.scope != command.scope or draft.epoch_id != command.target_epoch_id:
        return "target_missing"
    if draft.source_hash != command.expected_source_hash:
        return "source_mismatch"
    return lookup


async def discover_current_inventory(
    discovery: WorkflowDiscoveryUseCase,
    *,
    actor: str,
    scope: RepositoryScope,
) -> DiscoveryReport | CurrentInventoryRefusal:
    """Require one complete, locally closed current-default-head workflow inventory."""
    discovered = await discovery(actor=actor, scope=scope, revision=None)
    if isinstance(discovered, WorkflowDiscoveryForbidden):
        return "forbidden"
    if isinstance(discovered, WorkflowDiscoveryUnavailable):
        return "overloaded" if discovered.reason == "overloaded" else "unavailable"
    if not isinstance(discovered, WorkflowDiscoveryCompleted):
        return "unavailable"
    report = discovered.report
    if report.repository.scope != scope:
        return "unavailable"
    if not (report.complete and report.local_graph_closed):
        return "blocked"
    return report


class RegisteredEpochReviewService:
    """Accept one consumed attestation; callers own session and role admission."""

    def __init__(
        self,
        *,
        discovery: WorkflowDiscoveryUseCase,
        store: RegisteredEpochReviewStore,
        clock: Clock,
    ) -> None:
        self._discovery = discovery
        self._store = store
        self._clock = clock

    async def accept(
        self,
        command: RegisteredEpochReviewCommand,
        *,
        attestation: RegisteredEpochAttestationEvidence,
    ) -> RegisteredEpochReviewOutcome:
        if type(command) is not RegisteredEpochReviewCommand:
            raise TypeError("registered-epoch review service requires an exact command")
        if type(attestation) is not RegisteredEpochAttestationEvidence:
            raise TypeError("registered-epoch review requires exact attestation")
        binding = attestation.transaction.binding
        try:
            replay = await self._store.resolve_operation(command)
            if isinstance(replay, RegisteredEpochReviewDuplicate):
                return RegisteredEpochReviewOutcome("duplicate", record=replay.record)
            if isinstance(replay, RegisteredEpochReviewOperationConflict):
                return RegisteredEpochReviewOutcome("operation_conflict")
            active = await self._store.load_active(command.scope)
            if (None if active is None else active.active) != command.expected_active:
                return RegisteredEpochReviewOutcome("baseline_conflict")
            target = admit_registered_epoch_target(
                await self._store.load_registered_epoch(command.scope, command.target_epoch_id),
                command,
            )
        except ProposalReviewStoreUnavailable:
            return RegisteredEpochReviewOutcome("unavailable")
        if not isinstance(target, RegisteredEpochSubject):
            return RegisteredEpochReviewOutcome(target)

        report = await discover_current_inventory(
            self._discovery,
            actor=command.actor,
            scope=command.scope,
        )
        if not isinstance(report, DiscoveryReport):
            return RegisteredEpochReviewOutcome(report)
        if (
            report.revision != binding.revision
            or report.inventory_digest != binding.inventory_digest
        ):
            return RegisteredEpochReviewOutcome("stale")

        try:
            semantic_diff = compare_policy_drafts(
                None if active is None else active.draft,
                target.draft,
            )
        except SemanticDiffLimitExceeded:
            return RegisteredEpochReviewOutcome("diff_limit")
        except (TypeError, ValueError):
            return RegisteredEpochReviewOutcome("unavailable")
        prepared = prepare_registered_epoch_review(
            RegisteredEpochReviewDraft(
                command=command,
                provider_revision=report.revision,
                inventory_digest=report.inventory_digest,
                subject=target,
                semantic_diff=semantic_diff,
            ),
            attestation=attestation,
            occurred_at=_audit_timestamp(self._clock.now()),
        )
        try:
            result = await self._store.accept(prepared)
        except ProposalReviewStoreUnavailable:
            return RegisteredEpochReviewOutcome("unavailable")
        if isinstance(result, RegisteredEpochReviewCreated):
            return RegisteredEpochReviewOutcome("accepted", record=result.record)
        if isinstance(result, RegisteredEpochReviewDuplicate):
            return RegisteredEpochReviewOutcome("duplicate", record=result.record)
        if isinstance(result, RegisteredEpochReviewBaselineConflict):
            return RegisteredEpochReviewOutcome("baseline_conflict")
        if isinstance(result, RegisteredEpochReviewOperationConflict):
            return RegisteredEpochReviewOutcome("operation_conflict")
        if isinstance(result, RegisteredEpochReviewAttestationConflict):
            return RegisteredEpochReviewOutcome("attestation_conflict")
        raise RuntimeError("registered-epoch review result algebra is incomplete")


def _audit_timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("registered-epoch review clock must return a timezone-aware instant")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
