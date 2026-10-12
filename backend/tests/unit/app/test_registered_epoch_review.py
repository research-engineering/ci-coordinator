"""Start and callback witnesses for the registered-epoch review subject (CI-018)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

import pytest
from control_plane_http_support import human_principal
from registered_epoch_review_support import (
    REGISTRANT,
    RegisteredEpochStore,
    ReviewerProvider,
    ScriptedDiscovery,
    admitted_draft,
    discovered_inventory,
    registered_command,
    registered_review_record,
    registered_subject,
)
from repository_activation_support import NOW, SCOPE

from ci_coordinator.app.registered_epoch_review import admit_registered_epoch_target
from ci_coordinator.app.repository_attestation import RepositoryAttestationService
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.control_plane_identity import ControlPlaneIdentityCrypto
from ci_coordinator.kernel import FixedClock
from ci_coordinator.proposal_review import (
    ProposalReviewStore,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewDuplicate,
    RegisteredEpochReviewStore,
    RegisteredEpochWithoutReceipt,
)
from ci_coordinator.workflow_discovery import WorkflowDiscoveryOutcome

_PRINCIPAL = human_principal()
_PROFILE = _PRINCIPAL.authority_profile_digest
_DRAFT = admitted_draft()


class _Entropy:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self, size: int) -> bytes:
        self._value += 1
        return bytes([self._value]) * size


@dataclass
class _UntouchedProposalStore:
    """Any proposal-store effect on the registered subject path is a falsifier."""

    calls: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> object:
        async def record(*_args: object, **_kwargs: object) -> object:
            self.calls.append(name)
            raise AssertionError(f"registered subject touched proposal store: {name}")

        return record


def _service(
    store: RegisteredEpochStore,
    provider: ReviewerProvider,
    discovery: ScriptedDiscovery,
    proposal_store: _UntouchedProposalStore | None = None,
) -> RepositoryAttestationService:
    return RepositoryAttestationService(
        crypto=ControlPlaneIdentityCrypto(bytes(range(32)), entropy=_Entropy()),
        provider=provider,
        discovery=discovery,
        store=cast(ProposalReviewStore, proposal_store or _UntouchedProposalStore()),
        registered_epoch_store=cast(RegisteredEpochReviewStore, store),
        clock=FixedClock(NOW),
        authority_profile_digest=_PROFILE,
    )


def _discovery(*outcomes: WorkflowDiscoveryOutcome) -> ScriptedDiscovery:
    return ScriptedDiscovery(list(outcomes) or [discovered_inventory()])


def _command(source_hash: str | None = None) -> RegisteredEpochReviewCommand:
    return registered_command(_DRAFT, actor=_PRINCIPAL.actor_id, source_hash=source_hash)


def test_start_binds_registered_subject_revision_and_inventory_before_registration() -> None:
    inventory = discovered_inventory()
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    discovery = _discovery(inventory)
    command = _command()

    outcome = asyncio.run(
        _service(store, provider, discovery).start_registered_epoch(
            principal=_PRINCIPAL, command=command
        )
    )

    assert outcome.state == "ready"
    assert outcome.transaction_cookie is not None
    assert discovery.calls == [(_PRINCIPAL.actor_id, SCOPE, None)]
    transaction = store.pending
    assert transaction is not None
    binding = transaction.binding
    assert binding.target_epoch_id == _DRAFT.epoch_id
    assert binding.source_hash == _DRAFT.source_hash
    assert binding.revision == inventory.report.revision
    assert binding.inventory_digest == inventory.report.inventory_digest
    assert binding.initiating_actor == _PRINCIPAL.actor_id
    assert binding.operation_id == command.operation_id
    assert (binding.expected_active_epoch_id, binding.expected_active_revision) == (None, None)
    assert (binding.issued_at, binding.expires_at) == (NOW, NOW + timedelta(minutes=5))
    assert [call[0] if isinstance(call, tuple) else call for call in store.calls] == [
        "resolve",
        "active",
        "epoch",
        "current_time",
        "register",
    ]


@pytest.mark.parametrize(
    ("lookup", "source_hash", "expected"),
    [
        (None, None, "target_missing"),
        (RegisteredEpochWithoutReceipt(), None, "target_ineligible"),
        (registered_subject(_DRAFT), "0" * 64, "source_mismatch"),
    ],
    ids=("absent", "proposal-created-without-receipt", "source-hash-mismatch"),
)
def test_start_keeps_missing_ineligible_and_mismatched_targets_distinct_before_provider_io(
    lookup: object, source_hash: str | None, expected: str
) -> None:
    # Kills: mapping a receipt-less epoch to target_missing, or skipping the source-hash check.
    store = RegisteredEpochStore(lookup=cast(RegisteredEpochWithoutReceipt | None, lookup))
    provider = ReviewerProvider()
    discovery = _discovery()
    command = _command(source_hash)

    outcome = asyncio.run(
        _service(store, provider, discovery).start_registered_epoch(
            principal=_PRINCIPAL, command=command
        )
    )

    assert outcome.state == expected
    assert discovery.calls == []
    assert provider.authorization_calls == []
    assert store.pending is None


def test_foreign_scope_epoch_identity_is_missing_not_ineligible() -> None:
    foreign = admitted_draft(installation_id=3, repository_id=4)
    command = registered_command(foreign, actor=_PRINCIPAL.actor_id)
    lookup = registered_subject(foreign)

    assert command.scope == SCOPE
    assert foreign.scope == RepositoryScope(3, 4)
    assert admit_registered_epoch_target(lookup, command) == "target_missing"


@pytest.mark.parametrize("shape", ["incomplete", "open_graph"])
def test_start_refuses_incomplete_or_open_graph_inventory_as_blocked(shape: str) -> None:
    # Kills: dropping the complete or localGraphClosed conjunct of owner decision D5.
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    service = _service(store, provider, _discovery(discovered_inventory(shape=shape)))

    outcome = asyncio.run(service.start_registered_epoch(principal=_PRINCIPAL, command=_command()))

    assert outcome.state == "blocked"
    assert provider.authorization_calls == []
    assert store.pending is None


def test_exact_replay_returns_before_baseline_target_provider_and_pending() -> None:
    record = registered_review_record(discovered_inventory(), _DRAFT, actor=_PRINCIPAL.actor_id)
    store = RegisteredEpochStore(
        lookup=registered_subject(_DRAFT),
        resolution=RegisteredEpochReviewDuplicate(record),
    )
    provider = ReviewerProvider()
    discovery = _discovery()

    outcome = asyncio.run(
        _service(store, provider, discovery).start_registered_epoch(
            principal=_PRINCIPAL, command=record.command
        )
    )

    assert outcome.state == "already_reviewed"
    assert store.calls == [("resolve", record.command)]
    assert discovery.calls == []
    assert provider.authorization_calls == []


def test_start_baseline_conflict_stops_before_target_and_provider_io() -> None:
    store = RegisteredEpochStore(
        lookup=registered_subject(_DRAFT),
        active=ActiveConfigEpochSnapshot(ActiveConfigEpoch(SCOPE, _DRAFT.epoch_id, 1), _DRAFT),
    )
    discovery = _discovery()

    outcome = asyncio.run(
        _service(store, ReviewerProvider(), discovery).start_registered_epoch(
            principal=_PRINCIPAL, command=_command()
        )
    )

    assert outcome.state == "baseline_conflict"
    assert discovery.calls == []
    assert not any(isinstance(call, tuple) and call[0] == "epoch" for call in store.calls)


@pytest.mark.parametrize("stage", ["resolve", "active", "epoch", "time", "register"])
def test_storage_unavailable_at_each_start_stage_is_unavailable(stage: str) -> None:
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT), unavailable_at=stage)

    outcome = asyncio.run(
        _service(store, ReviewerProvider(), _discovery()).start_registered_epoch(
            principal=_PRINCIPAL, command=_command()
        )
    )

    assert outcome.state == "unavailable"
    assert store.pending is None


def _started(
    store: RegisteredEpochStore,
    provider: ReviewerProvider,
    discovery: ScriptedDiscovery,
    proposal_store: _UntouchedProposalStore | None = None,
) -> tuple[RepositoryAttestationService, str]:
    service = _service(store, provider, discovery, proposal_store)
    started = asyncio.run(service.start_registered_epoch(principal=_PRINCIPAL, command=_command()))
    assert started.state == "ready" and started.transaction_cookie is not None
    return service, started.transaction_cookie


def test_callback_registers_one_review_with_registrant_and_second_consume_is_replayed() -> None:
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    proposal_store = _UntouchedProposalStore()
    service, cookie = _started(store, provider, _discovery(), proposal_store)
    assert provider.state is not None

    first = asyncio.run(
        service.complete(
            principal=_PRINCIPAL, transaction_cookie=cookie, state=provider.state, code="c"
        )
    )
    second = asyncio.run(
        service.complete(
            principal=_PRINCIPAL, transaction_cookie=cookie, state=provider.state, code="c"
        )
    )

    assert first.state == "completed" and first.review is None
    review = first.registered_epoch_review
    assert review is not None and review.state == "accepted"
    assert review.record is not None
    assert review.record.registrant == REGISTRANT
    assert review.record.command.target_epoch_id == _DRAFT.epoch_id
    assert second.state == "replayed"
    assert len(provider.exchange_calls) == 1
    assert store.pending is None
    # Neither callback consumed, inspected or retired a proposal-subject challenge.
    assert proposal_store.calls == []


@pytest.mark.parametrize(
    ("callback_inventory", "expected"),
    [
        (discovered_inventory(revision="b" * 40), "stale"),
        (discovered_inventory(shape="changed"), "stale"),
        (discovered_inventory(revision="b" * 40, shape="open_graph"), "blocked"),
    ],
    ids=("head-moved", "inventory-changed-same-revision", "graph-opened"),
)
def test_callback_rediscovery_refuses_a_moved_head_or_changed_inventory(
    callback_inventory: WorkflowDiscoveryOutcome, expected: str
) -> None:
    # Kills: comparing only the revision, or only the inventory digest, at callback time.
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    service, cookie = _started(
        store, provider, _discovery(discovered_inventory(), callback_inventory)
    )
    assert provider.state is not None

    outcome = asyncio.run(
        service.complete(
            principal=_PRINCIPAL, transaction_cookie=cookie, state=provider.state, code="c"
        )
    )

    assert outcome.state == "completed"
    assert outcome.registered_epoch_review is not None
    assert outcome.registered_epoch_review.state == expected
    assert not any(isinstance(call, tuple) and call[0] == "accept" for call in store.calls)
    assert store.pending is None


def test_callback_baseline_moved_after_start_is_a_conflict_without_acceptance() -> None:
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    service, cookie = _started(store, provider, _discovery())
    store.active = ActiveConfigEpochSnapshot(ActiveConfigEpoch(SCOPE, _DRAFT.epoch_id, 1), _DRAFT)
    assert provider.state is not None

    outcome = asyncio.run(
        service.complete(
            principal=_PRINCIPAL, transaction_cookie=cookie, state=provider.state, code="c"
        )
    )

    assert outcome.registered_epoch_review is not None
    assert outcome.registered_epoch_review.state == "baseline_conflict"
    assert not any(isinstance(call, tuple) and call[0] == "accept" for call in store.calls)


def test_registered_challenge_at_its_exact_expiry_is_replayed_before_provider_exchange() -> None:
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    service, cookie = _started(store, provider, _discovery())
    assert store.pending is not None
    assert store.pending.binding.expires_at == NOW + timedelta(minutes=5)
    store.now = store.pending.binding.expires_at
    assert provider.state is not None

    outcome = asyncio.run(
        service.complete(
            principal=_PRINCIPAL, transaction_cookie=cookie, state=provider.state, code="c"
        )
    )

    assert outcome.state == "replayed"
    assert provider.exchange_calls == []
    assert store.pending is None


def test_review_alone_leaves_the_active_pointer_unchanged() -> None:
    store = RegisteredEpochStore(lookup=registered_subject(_DRAFT))
    provider = ReviewerProvider()
    service, cookie = _started(store, provider, _discovery())
    assert provider.state is not None

    outcome = asyncio.run(
        service.complete(
            principal=_PRINCIPAL, transaction_cookie=cookie, state=provider.state, code="c"
        )
    )

    assert outcome.registered_epoch_review is not None
    assert outcome.registered_epoch_review.state == "accepted"
    assert store.active is None
    assert not any(isinstance(call, tuple) and call[0] == "activate" for call in store.calls)
