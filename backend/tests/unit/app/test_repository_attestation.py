from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import cast

import pytest
from control_plane_http_support import NOW, human_principal
from repository_activation_support import REVIEWER, SCOPE, completed_discovery

from ci_coordinator.app.repository_attestation import (
    RepositoryAttestationService,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot
from ci_coordinator.control_plane_identity import (
    ControlPlaneIdentityCrypto,
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    KeycloakHumanPrincipal,
)
from ci_coordinator.kernel import FixedClock
from ci_coordinator.proposal_review import (
    GitHubReviewerRejected,
    GitHubReviewerUnavailable,
    PreparedProposalReview,
    ProposalReviewAttestationConflict,
    ProposalReviewCommand,
    ProposalReviewCreated,
    ProposalReviewRecord,
    ProposalReviewResolution,
    ProposalReviewStore,
    ProposalReviewWriteResult,
    RepositoryAttestationCapacityExceeded,
    RepositoryAttestationRegistered,
    RepositoryAttestationRegistration,
    RepositoryAttestationTransaction,
)
from ci_coordinator.workflow_discovery import WorkflowDiscoveryCompleted, WorkflowDiscoveryOutcome

_PRINCIPAL = human_principal()
_PROFILE = _PRINCIPAL.authority_profile_digest


class _Entropy:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self, size: int) -> bytes:
        self._value += 1
        return bytes([self._value]) * size


@dataclass
class _Provider:
    exchange_result: GitHubReviewerEvidence | Exception = REVIEWER
    state: str | None = None
    authorization_calls: list[tuple[str, str]] = field(default_factory=list)
    exchange_calls: list[tuple[str, str, RepositoryScope]] = field(default_factory=list)

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        self.state = state
        self.authorization_calls.append((state, code_challenge))
        return f"https://github.com/login/oauth/authorize?state={state}"

    async def exchange_and_resolve(
        self,
        *,
        code: str,
        code_verifier: str,
        scope: RepositoryScope,
    ) -> GitHubReviewerEvidence:
        self.exchange_calls.append((code, code_verifier, scope))
        if isinstance(self.exchange_result, Exception):
            raise self.exchange_result
        return self.exchange_result


@dataclass
class _Discovery:
    outcome: WorkflowDiscoveryOutcome
    calls: list[tuple[str, RepositoryScope, str | None]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome:
        self.calls.append((actor, scope, revision))
        return self.outcome


@dataclass
class _Store:
    now: datetime = NOW
    registration: RepositoryAttestationRegistration = field(
        default_factory=RepositoryAttestationRegistered
    )
    resolution: ProposalReviewResolution = None
    active: ActiveConfigEpochSnapshot | None = None
    pending: RepositoryAttestationTransaction | None = None
    calls: list[object] = field(default_factory=list)

    async def current_time(self) -> datetime:
        self.calls.append("current_time")
        return self.now

    async def register_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> RepositoryAttestationRegistration:
        self.calls.append(("register", transaction))
        if isinstance(self.registration, RepositoryAttestationRegistered):
            self.pending = transaction
        return self.registration

    async def attestation_is_pending(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> bool:
        self.calls.append(("pending", transaction))
        return self.pending == transaction

    async def retire_attestation(self, transaction: RepositoryAttestationTransaction) -> None:
        self.calls.append(("retire", transaction))
        if self.pending == transaction:
            self.pending = None

    async def resolve_operation(
        self,
        command: ProposalReviewCommand,
    ) -> ProposalReviewResolution:
        self.calls.append(("resolve", command))
        return self.resolution

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self.calls.append(("active", scope))
        return self.active

    async def accept(self, prepared: PreparedProposalReview) -> ProposalReviewWriteResult:
        self.calls.append(("accept", prepared))
        if self.pending != prepared.attestation.transaction:
            return ProposalReviewAttestationConflict()
        self.pending = None
        record = ProposalReviewRecord.from_draft(
            prepared.draft,
            attestation=prepared.attestation,
            audit_event_id="audit_" + "5" * 32,
            audit_input_hash=prepared.audit_input_hash,
        )
        return ProposalReviewCreated(record, epoch_created=True)


def test_start_binds_every_authority_coordinate_before_registration() -> None:
    completed = completed_discovery()
    store = _Store()
    provider = _Provider()
    discovery = _Discovery(completed)
    service = _service(store, provider, discovery)
    command = _command(completed)

    outcome = asyncio.run(service.start(principal=_PRINCIPAL, command=command))

    assert outcome.state == "ready"
    assert outcome.transaction_cookie is not None
    assert outcome.authorization_url is not None
    assert provider.authorization_calls and len(provider.authorization_calls[0][1]) == 43
    assert discovery.calls == [(_PRINCIPAL.actor_id, SCOPE, None)]
    transaction = store.pending
    assert transaction is not None
    binding = transaction.binding
    assert binding.initiating_actor == _PRINCIPAL.actor_id
    assert binding.scope == SCOPE
    assert binding.operation_id == command.operation_id
    assert binding.proposal_manifest_id == completed.proposal.manifest_id
    assert binding.revision == completed.report.revision
    assert binding.proposal_digest == completed.proposal.proposal_digest
    assert binding.authority_profile_digest == _PROFILE
    assert (binding.issued_at, binding.expires_at) == (NOW, NOW + timedelta(minutes=5))


def test_callback_consumes_one_exact_transaction_and_replay_stops_before_provider() -> None:
    completed = completed_discovery()
    store = _Store()
    provider = _Provider()
    service = _service(store, provider, _Discovery(completed))
    started = asyncio.run(service.start(principal=_PRINCIPAL, command=_command(completed)))
    assert started.transaction_cookie is not None and provider.state is not None

    first = asyncio.run(
        service.complete(
            principal=_PRINCIPAL,
            transaction_cookie=started.transaction_cookie,
            state=provider.state,
            code="provider-code",
        )
    )
    second = asyncio.run(
        service.complete(
            principal=_PRINCIPAL,
            transaction_cookie=started.transaction_cookie,
            state=provider.state,
            code="provider-code",
        )
    )

    assert first.state == "completed"
    assert first.review is not None and first.review.state == "accepted"
    assert first.review.record is not None
    attestation = first.review.record.attestation
    assert attestation.transaction.binding.initiating_actor == _PRINCIPAL.actor_id
    assert attestation.reviewer == GitHubReviewerPrincipal(
        evidence=REVIEWER,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    assert provider.exchange_calls[0][0] == "provider-code"
    assert len(provider.exchange_calls[0][1]) == 43
    assert provider.exchange_calls[0][2] == SCOPE
    assert second.state == "replayed"
    assert len(provider.exchange_calls) == 1
    assert store.pending is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"state": "wrong-state"},
        {"principal": human_principal(roles=frozenset({"read"}))},
        {"principal": replace(_PRINCIPAL, authority_profile_digest="3" * 64)},
    ],
    ids=("state", "role", "profile"),
)
def test_callback_rejects_changed_authority_before_provider(
    mutation: dict[str, object],
) -> None:
    completed = completed_discovery()
    store = _Store()
    provider = _Provider()
    service = _service(store, provider, _Discovery(completed))
    started = asyncio.run(service.start(principal=_PRINCIPAL, command=_command(completed)))
    assert started.transaction_cookie is not None and provider.state is not None
    principal = cast(KeycloakHumanPrincipal, mutation.get("principal", _PRINCIPAL))
    state = cast(str, mutation.get("state", provider.state))

    outcome = asyncio.run(
        service.complete(
            principal=principal,
            transaction_cookie=started.transaction_cookie,
            state=state,
            code="provider-code",
        )
    )

    assert outcome.state == "invalid"
    assert provider.exchange_calls == []
    assert store.pending is None


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        (GitHubReviewerRejected("provider rejected"), "invalid"),
        (GitHubReviewerUnavailable("provider unavailable"), "unavailable"),
    ],
    ids=("rejected", "unavailable"),
)
def test_terminal_provider_failure_retires_the_exact_pending_transaction(
    failure: Exception,
    expected_state: str,
) -> None:
    completed = completed_discovery()
    store = _Store()
    provider = _Provider(exchange_result=failure)
    service = _service(store, provider, _Discovery(completed))
    started = asyncio.run(service.start(principal=_PRINCIPAL, command=_command(completed)))
    assert started.transaction_cookie is not None and provider.state is not None

    outcome = asyncio.run(
        service.complete(
            principal=_PRINCIPAL,
            transaction_cookie=started.transaction_cookie,
            state=provider.state,
            code="provider-code",
        )
    )

    assert outcome.state == expected_state
    assert store.pending is None
    assert any(call[0] == "retire" for call in store.calls if isinstance(call, tuple))


def test_start_capacity_and_expired_session_are_distinct_fail_closed_states() -> None:
    completed = completed_discovery()
    command = _command(completed)
    capacity = asyncio.run(
        _service(
            _Store(registration=RepositoryAttestationCapacityExceeded()),
            _Provider(),
            _Discovery(completed),
        ).start(principal=_PRINCIPAL, command=command)
    )
    expired = asyncio.run(
        _service(
            _Store(now=_PRINCIPAL.expires_at),
            _Provider(),
            _Discovery(completed),
        ).start(principal=_PRINCIPAL, command=command)
    )

    assert capacity.state == "overloaded"
    assert expired.state == "forbidden"


def _service(
    store: _Store,
    provider: _Provider,
    discovery: _Discovery,
) -> RepositoryAttestationService:
    return RepositoryAttestationService(
        crypto=ControlPlaneIdentityCrypto(bytes(range(32)), entropy=_Entropy()),
        provider=provider,
        discovery=discovery,
        store=cast(ProposalReviewStore, store),
        clock=FixedClock(NOW),
        authority_profile_digest=_PROFILE,
    )


def _command(completed: WorkflowDiscoveryCompleted) -> ProposalReviewCommand:
    return ProposalReviewCommand(
        scope=SCOPE,
        operation_id="review-1",
        expected_manifest_id=completed.proposal.manifest_id,
        expected_active=None,
        actor=_PRINCIPAL.actor_id,
    )
