from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
from repository_activation_support import ACTOR, SCOPE, review_record

import ci_coordinator.app.proposal_review as proposal_review_app
from ci_coordinator.app.proposal_review import ProposalReviewOutcome, ProposalReviewService
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.kernel import FixedClock
from ci_coordinator.proposal_review import (
    PreparedProposalReview,
    ProposalReviewBaselineConflict,
    ProposalReviewCommand,
    ProposalReviewCreated,
    ProposalReviewDuplicate,
    ProposalReviewEpochConflict,
    ProposalReviewOperationConflict,
    ProposalReviewRecord,
    ProposalReviewResolution,
    ProposalReviewStore,
    ProposalReviewStoreUnavailable,
    ProposalReviewWriteResult,
    SemanticDiffLimitExceeded,
)
from ci_coordinator.workflow_discovery import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryInvalidRequest,
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryUnavailable,
)
from ci_coordinator.workflow_discovery.service import WorkflowDiscoveryService
from ci_coordinator.workflow_discovery.source import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowSource,
    git_blob_sha1,
)

NOW = datetime(2026, 7, 19, 10, tzinfo=UTC)


def test_forbidden_review_reaches_neither_store_nor_provider() -> None:
    completed = _completed()
    store = _Store()
    discovery = _Discovery(completed)
    service = _service(False, store, discovery)

    outcome = _accept(service, _command(completed), completed)

    assert outcome.state == "forbidden"
    assert store.calls == []
    assert discovery.calls == 0


@pytest.mark.parametrize("conflict", [False, True], ids=["exact-replay", "operation-conflict"])
def test_operation_resolution_precedes_active_read_and_provider_io(conflict: bool) -> None:
    completed = _completed()
    retained = _record(completed)
    resolution: ProposalReviewResolution = (
        ProposalReviewOperationConflict(retained) if conflict else ProposalReviewDuplicate(retained)
    )
    store = _Store(resolution=resolution)
    discovery = _Discovery(completed)

    outcome = _accept(_service(True, store, discovery), _command(completed), completed)

    assert outcome.state == ("operation_conflict" if conflict else "duplicate")
    assert store.calls == ["resolve"]
    assert discovery.calls == 0


def test_changed_active_baseline_stops_before_provider_io() -> None:
    completed = _completed()
    proposal = completed.proposal
    assert proposal.admission is not None
    active = ActiveConfigEpochSnapshot(
        ActiveConfigEpoch(SCOPE, proposal.admission.epoch_id, 2),
        proposal.admission,
    )
    store = _Store(active=active)
    discovery = _Discovery(completed)

    outcome = _accept(_service(True, store, discovery), _command(completed), completed)

    assert outcome.state == "baseline_conflict"
    assert store.calls == ["resolve", "load_active"]
    assert discovery.calls == 0


def test_changed_current_manifest_is_stale_and_writes_nothing() -> None:
    observed = _completed(revision="a" * 40, signal="Full CI")
    current = _completed(revision="b" * 40, signal="Verified CI")
    store = _Store()

    outcome = _accept(_service(True, store, _Discovery(current)), _command(observed), observed)

    assert outcome.state == "stale"
    assert store.calls == ["resolve", "load_active"]


@pytest.mark.parametrize(
    ("discovery_outcome", "expected_state"),
    [
        (WorkflowDiscoveryForbidden(), "forbidden"),
        (WorkflowDiscoveryInvalidRequest("invalid_revision"), "unavailable"),
        (WorkflowDiscoveryUnavailable("overloaded"), "overloaded"),
        (WorkflowDiscoveryUnavailable("provider_unavailable"), "unavailable"),
    ],
    ids=["forbidden", "invalid", "overloaded", "unavailable"],
)
def test_discovery_failures_write_nothing(
    discovery_outcome: WorkflowDiscoveryOutcome,
    expected_state: str,
) -> None:
    completed = _completed()
    store = _Store()

    outcome = _accept(
        _service(True, store, _Discovery(discovery_outcome)),
        _command(completed),
        completed,
    )

    assert outcome.state == expected_state
    assert store.calls == ["resolve", "load_active"]


def test_historical_discovery_is_blocked() -> None:
    completed = _completed()
    historical = _completed(default_branch_head=False)
    store = _Store()

    outcome = _accept(
        _service(True, store, _Discovery(historical)),
        _command(completed),
        completed,
    )

    assert outcome.state == "blocked"
    assert store.calls == ["resolve", "load_active"]


def test_diff_limit_precedes_clock_and_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = _completed()
    store = _Store()
    discovery = _Discovery(completed)

    def reject_diff(*_args: object) -> None:
        raise SemanticDiffLimitExceeded("test limit")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(proposal_review_app, "compare_policy_drafts", reject_diff)
    service = ProposalReviewService(
        authorizer=_Authorizer(True),
        discovery=discovery,
        store=cast(ProposalReviewStore, store),
        clock=_UnreachableClock(),
    )

    outcome = _accept(service, _command(completed), completed)

    assert outcome.state == "diff_limit"
    assert store.calls == ["resolve", "load_active"]
    assert discovery.calls == 1


@pytest.mark.parametrize(
    ("unavailable_at", "expected_calls", "provider_calls"),
    [
        ("resolve", ["resolve"], 0),
        ("load_active", ["resolve", "load_active"], 0),
        ("accept", ["resolve", "load_active", "accept"], 1),
    ],
)
def test_store_unavailability_is_fail_closed(
    unavailable_at: str,
    expected_calls: list[str],
    provider_calls: int,
) -> None:
    completed = _completed()
    store = _Store(unavailable_at=unavailable_at)
    discovery = _Discovery(completed)

    outcome = _accept(_service(True, store, discovery), _command(completed), completed)

    assert outcome.state == "unavailable"
    assert store.calls == expected_calls
    assert discovery.calls == provider_calls


def test_exact_current_proposal_is_prepared_and_result_is_projected() -> None:
    completed = _completed()
    store = _Store(write_result="created")

    outcome = _accept(
        _service(True, store, _Discovery(completed)),
        _command(completed),
        completed,
    )

    assert outcome.state == "accepted"
    assert outcome.epoch_created is True
    assert outcome.record is not None
    assert outcome.record.command.expected_manifest_id == completed.proposal.manifest_id
    assert store.calls == ["resolve", "load_active", "accept"]


def test_unknown_commit_retry_resolves_exact_operation_before_provider_io() -> None:
    completed = _completed()
    store = _Store(unknown_commit_once=True)
    discovery = _Discovery(completed)
    service = _service(True, store, discovery)
    command = _command(completed)

    first = _accept(service, command, completed)
    retry = _accept(service, command, completed)

    assert first.state == "unavailable"
    assert retry.state == "duplicate"
    assert retry.record is not None
    assert retry.record.command == command
    assert store.calls == ["resolve", "load_active", "accept", "resolve"]
    assert discovery.calls == 1


def test_lock_time_baseline_conflict_is_not_reported_as_acceptance() -> None:
    completed = _completed()
    store = _Store(write_result="baseline_conflict")

    outcome = _accept(
        _service(True, store, _Discovery(completed)),
        _command(completed),
        completed,
    )

    assert outcome.state == "baseline_conflict"
    assert outcome.record is None


@pytest.mark.parametrize(
    ("write_result", "expected_state", "retains_record"),
    [
        ("duplicate", "duplicate", True),
        ("operation_conflict", "operation_conflict", False),
        ("epoch_conflict", "epoch_conflict", False),
    ],
)
def test_write_result_algebra_is_projected(
    write_result: str,
    expected_state: str,
    retains_record: bool,
) -> None:
    completed = _completed()

    outcome = _accept(
        _service(True, _Store(write_result=write_result), _Discovery(completed)),
        _command(completed),
        completed,
    )

    assert outcome.state == expected_state
    assert (outcome.record is not None) is retains_record


@pytest.mark.parametrize(
    ("state", "has_record", "epoch_created", "error"),
    [
        ("unknown", False, None, "state"),
        ("accepted", False, None, "retained record"),
        ("blocked", True, None, "retained record"),
        ("duplicate", True, True, "newly accepted"),
    ],
)
def test_outcome_algebra_rejects_inconsistent_states(
    state: object,
    has_record: bool,
    epoch_created: bool | None,
    error: str,
) -> None:
    record = _record(_completed()) if has_record else None
    with pytest.raises(ValueError, match=error):
        ProposalReviewOutcome(
            state,  # type: ignore[arg-type]
            record=record,
            epoch_created=epoch_created,
        )


@dataclass
class _Authorizer:
    allowed: bool

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        del actor, scope
        return self.allowed


@dataclass
class _Discovery:
    outcome: WorkflowDiscoveryOutcome
    calls: int = 0

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome:
        del actor, scope
        assert revision is None
        self.calls += 1
        return self.outcome


class _Store:
    def __init__(
        self,
        *,
        resolution: ProposalReviewResolution = None,
        active: ActiveConfigEpochSnapshot | None = None,
        write_result: str = "created",
        unavailable_at: str | None = None,
        unknown_commit_once: bool = False,
    ) -> None:
        self.resolution = resolution
        self.active = active
        self.write_result = write_result
        self.unavailable_at = unavailable_at
        self.unknown_commit_once = unknown_commit_once
        self.calls: list[str] = []

    async def resolve_operation(self, command: ProposalReviewCommand) -> ProposalReviewResolution:
        del command
        self.calls.append("resolve")
        self._raise_if_unavailable("resolve")
        return self.resolution

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        del scope
        self.calls.append("load_active")
        self._raise_if_unavailable("load_active")
        return self.active

    async def accept(self, prepared: PreparedProposalReview) -> ProposalReviewWriteResult:
        self.calls.append("accept")
        self._raise_if_unavailable("accept")
        record = ProposalReviewRecord.from_draft(
            prepared.draft,
            attestation=prepared.attestation,
            audit_event_id="audit_" + "d" * 32,
            audit_input_hash=prepared.audit_input_hash,
        )
        if self.unknown_commit_once:
            self.unknown_commit_once = False
            self.resolution = ProposalReviewDuplicate(record)
            raise ProposalReviewStoreUnavailable("commit acknowledgement was lost")
        if self.write_result == "baseline_conflict":
            return ProposalReviewBaselineConflict(None)
        if self.write_result == "duplicate":
            return ProposalReviewDuplicate(record)
        if self.write_result == "operation_conflict":
            return ProposalReviewOperationConflict(record)
        if self.write_result == "epoch_conflict":
            return ProposalReviewEpochConflict(record.target_epoch_id)
        return ProposalReviewCreated(record, epoch_created=True)

    def _raise_if_unavailable(self, operation: str) -> None:
        if self.unavailable_at == operation:
            raise ProposalReviewStoreUnavailable("test store unavailable")


class _UnreachableClock:
    def now(self) -> datetime:
        raise AssertionError("diff limit must precede the clock")


def _service(
    allowed: bool,
    store: _Store,
    discovery: _Discovery,
) -> ProposalReviewService:
    return ProposalReviewService(
        authorizer=_Authorizer(allowed),
        discovery=discovery,
        store=cast(ProposalReviewStore, store),
        clock=FixedClock(NOW),
    )


def _command(completed: WorkflowDiscoveryCompleted) -> ProposalReviewCommand:
    return ProposalReviewCommand(
        scope=SCOPE,
        operation_id="review-1",
        expected_manifest_id=completed.proposal.manifest_id,
        expected_active=None,
        actor=ACTOR,
    )


def _record(completed: WorkflowDiscoveryCompleted) -> ProposalReviewRecord:
    proposal = completed.proposal
    assert proposal.admission is not None
    from ci_coordinator.proposal_review import ProposalReviewDraft, compare_policy_drafts

    draft = ProposalReviewDraft(
        command=_command(completed),
        provider_revision=completed.report.revision,
        inventory_digest=completed.report.inventory_digest,
        proposal_digest=proposal.proposal_digest,
        target=proposal.admission,
        semantic_diff=compare_policy_drafts(None, proposal.admission),
    )
    return ProposalReviewRecord.from_draft(
        draft,
        attestation=review_record(completed).attestation,
        audit_event_id="audit_" + "d" * 32,
        audit_input_hash="e" * 64,
    )


def _completed(
    *,
    revision: str = "a" * 40,
    signal: str = "Full CI",
    default_branch_head: bool = True,
) -> WorkflowDiscoveryCompleted:
    content = f"""\
name: CI
on:
  workflow_dispatch:
  push:
jobs:
  full:
    name: {signal}
    runs-on: ubuntu-latest
    steps:
      - run: python -m pytest
""".encode()
    source = WorkflowSource(
        ".github/workflows/ci.yml",
        git_blob_sha1(content),
        len(content),
        content,
    )
    snapshot = RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=revision,
        sources=(source,),
    )
    service = WorkflowDiscoveryService(authorizer=_Authorizer(True), reader=_Reader(snapshot))
    result = asyncio.run(
        service(
            actor=ACTOR,
            scope=SCOPE,
            revision=None if default_branch_head else revision,
        )
    )
    assert isinstance(result, WorkflowDiscoveryCompleted)
    assert result.proposal.state == ("reviewable" if default_branch_head else "blocked")
    return result


def _accept(
    service: ProposalReviewService,
    command: ProposalReviewCommand,
    completed: WorkflowDiscoveryCompleted,
) -> ProposalReviewOutcome:
    return asyncio.run(
        service.accept(
            command,
            attestation=review_record(completed).attestation,
        )
    )


@dataclass
class _Reader:
    snapshot: RepositoryWorkflowSnapshot

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> RepositoryWorkflowSnapshot:
        del scope, revision
        return self.snapshot
