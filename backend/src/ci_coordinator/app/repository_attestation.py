"""Keycloak-bound GitHub reviewer step-up for one proposal or registered-epoch review."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Protocol

from ci_coordinator.app.proposal_review import ProposalReviewOutcome, ProposalReviewService
from ci_coordinator.app.registered_epoch_review import (
    RegisteredEpochReviewOutcome,
    RegisteredEpochReviewService,
    admit_registered_epoch_target,
    discover_current_inventory,
)
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import ActiveConfigEpoch
from ci_coordinator.control_plane_identity import (
    ControlPlaneIdentityCrypto,
    GitHubReviewerPrincipal,
    KeycloakHumanPrincipal,
    RegisteredEpochReviewerOAuthTransaction,
    RegisteredEpochStepUpBinding,
    ReviewerOAuthTransaction,
    ReviewerStepUpBinding,
)
from ci_coordinator.kernel import Clock
from ci_coordinator.proposal_review import (
    GitHubReviewerProvider,
    GitHubReviewerRejected,
    GitHubReviewerUnavailable,
    ProposalReviewCommand,
    ProposalReviewDuplicate,
    ProposalReviewOperationConflict,
    ProposalReviewStore,
    ProposalReviewStoreUnavailable,
    RegisteredEpochAttestationEvidence,
    RegisteredEpochAttestationTransaction,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewDuplicate,
    RegisteredEpochReviewOperationConflict,
    RegisteredEpochReviewStore,
    RepositoryAttestationCapacityExceeded,
    RepositoryAttestationEvidence,
    RepositoryAttestationRegistered,
    RepositoryAttestationRegistration,
    RepositoryAttestationTransaction,
)
from ci_coordinator.workflow_discovery import (
    DiscoveryReport,
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryUnavailable,
    WorkflowDiscoveryUseCase,
)

type RepositoryAttestationStartState = Literal[
    "ready",
    "forbidden",
    "target_missing",
    "target_ineligible",
    "source_mismatch",
    "blocked",
    "stale",
    "baseline_conflict",
    "operation_conflict",
    "already_reviewed",
    "legacy_new_operation_unsupported",
    "overloaded",
    "unavailable",
]
type RepositoryAttestationCallbackState = Literal[
    "completed",
    "invalid",
    "replayed",
    "unavailable",
]

_START_STATES = frozenset(
    {
        "ready",
        "forbidden",
        "target_missing",
        "target_ineligible",
        "source_mismatch",
        "blocked",
        "stale",
        "baseline_conflict",
        "operation_conflict",
        "already_reviewed",
        "legacy_new_operation_unsupported",
        "overloaded",
        "unavailable",
    }
)
_CALLBACK_STATES = frozenset({"completed", "invalid", "replayed", "unavailable"})
_ATTESTATION_LIFETIME = timedelta(seconds=300)


@dataclass(frozen=True, slots=True)
class RepositoryAttestationStartOutcome:
    state: RepositoryAttestationStartState
    authorization_url: str | None = None
    transaction_cookie: str | None = None

    def __post_init__(self) -> None:
        if self.state not in _START_STATES:
            raise ValueError("repository-attestation start state is invalid")
        ready = self.state == "ready"
        if ready != (
            type(self.authorization_url) is str
            and bool(self.authorization_url)
            and type(self.transaction_cookie) is str
            and bool(self.transaction_cookie)
        ):
            raise ValueError("repository-attestation start output is inconsistent")


@dataclass(frozen=True, slots=True)
class RepositoryAttestationCallbackOutcome:
    state: RepositoryAttestationCallbackState
    review: ProposalReviewOutcome | None = None
    registered_epoch_review: RegisteredEpochReviewOutcome | None = None

    def __post_init__(self) -> None:
        if self.state not in _CALLBACK_STATES:
            raise ValueError("repository-attestation callback state is invalid")
        completed_subjects = (type(self.review) is ProposalReviewOutcome) + (
            type(self.registered_epoch_review) is RegisteredEpochReviewOutcome
        )
        if (self.review is not None and type(self.review) is not ProposalReviewOutcome) or (
            self.registered_epoch_review is not None
            and type(self.registered_epoch_review) is not RegisteredEpochReviewOutcome
        ):
            raise TypeError("repository-attestation callback review outcome must be exact")
        if completed_subjects != (1 if self.state == "completed" else 0):
            raise ValueError("repository-attestation callback output is inconsistent")


class RepositoryAttestationUseCase(Protocol):
    async def start(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        command: ProposalReviewCommand,
        legacy_replay_only: bool = False,
    ) -> RepositoryAttestationStartOutcome: ...

    async def start_registered_epoch(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        command: RegisteredEpochReviewCommand,
    ) -> RepositoryAttestationStartOutcome: ...

    async def complete(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        transaction_cookie: str | None,
        state: str | None,
        code: str,
    ) -> RepositoryAttestationCallbackOutcome: ...


class RepositoryAttestationService:
    def __init__(
        self,
        *,
        crypto: ControlPlaneIdentityCrypto,
        provider: GitHubReviewerProvider,
        discovery: WorkflowDiscoveryUseCase,
        store: ProposalReviewStore,
        registered_epoch_store: RegisteredEpochReviewStore,
        clock: Clock,
        authority_profile_digest: str,
    ) -> None:
        if type(crypto) is not ControlPlaneIdentityCrypto:
            raise TypeError("repository attestation requires exact identity crypto")
        if (
            type(authority_profile_digest) is not str
            or len(authority_profile_digest) != 64
            or any(character not in "0123456789abcdef" for character in authority_profile_digest)
        ):
            raise ValueError("repository-attestation authority profile is invalid")
        self._crypto = crypto
        self._provider = provider
        self._discovery = discovery
        self._store = store
        self._registered_epoch_store = registered_epoch_store
        self._clock = clock
        self._authority_profile_digest = authority_profile_digest

    async def start(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        command: ProposalReviewCommand,
        legacy_replay_only: bool = False,
    ) -> RepositoryAttestationStartOutcome:
        if (
            type(principal) is not KeycloakHumanPrincipal
            or type(command) is not ProposalReviewCommand
        ):
            raise TypeError("repository-attestation start requires exact inputs")
        if (
            command.actor != principal.actor_id
            or "configure" not in principal.roles
            or principal.authority_profile_digest != self._authority_profile_digest
        ):
            return RepositoryAttestationStartOutcome("forbidden")
        session_digest = self._crypto.handle_digest(principal.session_handle)
        if session_digest is None:
            return RepositoryAttestationStartOutcome("forbidden")
        try:
            replay = await self._store.resolve_operation(command)
            if isinstance(replay, ProposalReviewDuplicate):
                return RepositoryAttestationStartOutcome("already_reviewed")
            if isinstance(replay, ProposalReviewOperationConflict):
                return RepositoryAttestationStartOutcome("operation_conflict")
            if legacy_replay_only:
                return RepositoryAttestationStartOutcome("legacy_new_operation_unsupported")
            active = await self._store.load_active(command.scope)
        except ProposalReviewStoreUnavailable:
            return RepositoryAttestationStartOutcome("unavailable")
        if (None if active is None else active.active) != command.expected_active:
            return RepositoryAttestationStartOutcome("baseline_conflict")

        discovered = await self._discovery(
            actor=principal.actor_id,
            scope=command.scope,
            revision=None,
        )
        if isinstance(discovered, WorkflowDiscoveryForbidden):
            return RepositoryAttestationStartOutcome("forbidden")
        if isinstance(discovered, WorkflowDiscoveryUnavailable):
            return RepositoryAttestationStartOutcome(
                "overloaded" if discovered.reason == "overloaded" else "unavailable"
            )
        if not isinstance(discovered, WorkflowDiscoveryCompleted):
            return RepositoryAttestationStartOutcome("unavailable")
        proposal = discovered.proposal
        if proposal.state != "reviewable" or type(proposal.admission) is not ValidatedEpochDraft:
            return RepositoryAttestationStartOutcome("blocked")
        if proposal.manifest_id != command.expected_manifest_id:
            return RepositoryAttestationStartOutcome("stale")

        try:
            issued_at = await self._store.current_time()
        except ProposalReviewStoreUnavailable:
            return RepositoryAttestationStartOutcome("unavailable")
        expires_at = min(issued_at + _ATTESTATION_LIFETIME, principal.expires_at)
        if expires_at <= issued_at:
            return RepositoryAttestationStartOutcome("forbidden")
        expected_active = command.expected_active
        binding = ReviewerStepUpBinding(
            session_handle_digest=session_digest,
            initiating_actor=principal.actor_id,
            scope=command.scope,
            operation_id=command.operation_id,
            proposal_manifest_id=command.expected_manifest_id,
            revision=discovered.report.revision,
            proposal_digest=proposal.proposal_digest,
            expected_active_epoch_id=(
                None if expected_active is None else expected_active.epoch_id
            ),
            expected_active_revision=(
                None if expected_active is None else expected_active.revision
            ),
            authority_profile_digest=self._authority_profile_digest,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        request = self._crypto.new_reviewer_transaction(binding)
        transaction = RepositoryAttestationTransaction(
            transaction_digest=request.transaction_digest,
            binding=binding,
        )
        try:
            authorization_url = self._provider.authorization_url(
                state=request.state,
                code_challenge=request.code_challenge,
            )
            registration = await self._store.register_attestation(transaction)
        except (GitHubReviewerUnavailable, ProposalReviewStoreUnavailable, ValueError):
            return RepositoryAttestationStartOutcome("unavailable")
        if isinstance(registration, RepositoryAttestationCapacityExceeded):
            return RepositoryAttestationStartOutcome("overloaded")
        if not isinstance(registration, RepositoryAttestationRegistered):
            return RepositoryAttestationStartOutcome("forbidden")
        return RepositoryAttestationStartOutcome(
            "ready",
            authorization_url=authorization_url,
            transaction_cookie=request.transaction_cookie,
        )

    async def start_registered_epoch(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        command: RegisteredEpochReviewCommand,
    ) -> RepositoryAttestationStartOutcome:
        if (
            type(principal) is not KeycloakHumanPrincipal
            or type(command) is not RegisteredEpochReviewCommand
        ):
            raise TypeError("registered-epoch attestation start requires exact inputs")
        if (
            command.actor != principal.actor_id
            or "configure" not in principal.roles
            or principal.authority_profile_digest != self._authority_profile_digest
        ):
            return RepositoryAttestationStartOutcome("forbidden")
        session_digest = self._crypto.handle_digest(principal.session_handle)
        if session_digest is None:
            return RepositoryAttestationStartOutcome("forbidden")
        store = self._registered_epoch_store
        try:
            replay = await store.resolve_operation(command)
            if isinstance(replay, RegisteredEpochReviewDuplicate):
                return RepositoryAttestationStartOutcome("already_reviewed")
            if isinstance(replay, RegisteredEpochReviewOperationConflict):
                return RepositoryAttestationStartOutcome("operation_conflict")
            active = await store.load_active(command.scope)
            if (None if active is None else active.active) != command.expected_active:
                return RepositoryAttestationStartOutcome("baseline_conflict")
            target = admit_registered_epoch_target(
                await store.load_registered_epoch(command.scope, command.target_epoch_id),
                command,
            )
        except ProposalReviewStoreUnavailable:
            return RepositoryAttestationStartOutcome("unavailable")
        if isinstance(target, str):
            return RepositoryAttestationStartOutcome(target)

        report = await discover_current_inventory(
            self._discovery,
            actor=principal.actor_id,
            scope=command.scope,
        )
        if not isinstance(report, DiscoveryReport):
            return RepositoryAttestationStartOutcome(report)
        try:
            issued_at = await store.current_time()
        except ProposalReviewStoreUnavailable:
            return RepositoryAttestationStartOutcome("unavailable")
        expires_at = min(issued_at + _ATTESTATION_LIFETIME, principal.expires_at)
        if expires_at <= issued_at:
            return RepositoryAttestationStartOutcome("forbidden")
        expected_active = command.expected_active
        binding = RegisteredEpochStepUpBinding(
            session_handle_digest=session_digest,
            initiating_actor=principal.actor_id,
            scope=command.scope,
            operation_id=command.operation_id,
            target_epoch_id=command.target_epoch_id,
            source_hash=command.expected_source_hash,
            revision=report.revision,
            inventory_digest=report.inventory_digest,
            expected_active_epoch_id=(
                None if expected_active is None else expected_active.epoch_id
            ),
            expected_active_revision=(
                None if expected_active is None else expected_active.revision
            ),
            authority_profile_digest=self._authority_profile_digest,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        request = self._crypto.new_registered_epoch_reviewer_transaction(binding)
        try:
            authorization_url = self._provider.authorization_url(
                state=request.state,
                code_challenge=request.code_challenge,
            )
            registration: RepositoryAttestationRegistration = await store.register_attestation(
                RegisteredEpochAttestationTransaction(
                    transaction_digest=request.transaction_digest,
                    binding=binding,
                )
            )
        except (GitHubReviewerUnavailable, ProposalReviewStoreUnavailable, ValueError):
            return RepositoryAttestationStartOutcome("unavailable")
        if isinstance(registration, RepositoryAttestationCapacityExceeded):
            return RepositoryAttestationStartOutcome("overloaded")
        if not isinstance(registration, RepositoryAttestationRegistered):
            return RepositoryAttestationStartOutcome("forbidden")
        return RepositoryAttestationStartOutcome(
            "ready",
            authorization_url=authorization_url,
            transaction_cookie=request.transaction_cookie,
        )

    async def complete(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        transaction_cookie: str | None,
        state: str | None,
        code: str,
    ) -> RepositoryAttestationCallbackOutcome:
        if type(principal) is not KeycloakHumanPrincipal or type(code) is not str or not code:
            return RepositoryAttestationCallbackOutcome("invalid")
        oauth = self._crypto.open_reviewer_transaction(transaction_cookie)
        session_digest = self._crypto.handle_digest(principal.session_handle)
        if oauth is None:
            registered = self._crypto.open_registered_epoch_reviewer_transaction(transaction_cookie)
            if registered is None:
                return RepositoryAttestationCallbackOutcome("invalid")
            registered_transaction = RegisteredEpochAttestationTransaction(
                transaction_digest=registered.state_digest,
                binding=registered.binding,
            )
            try:
                return await self._complete_registered_epoch(
                    principal=principal,
                    oauth=registered,
                    transaction=registered_transaction,
                    session_digest=session_digest,
                    state=state,
                    code=code,
                )
            finally:
                await self._retire_registered_epoch(registered_transaction)
        transaction = RepositoryAttestationTransaction(
            transaction_digest=oauth.state_digest,
            binding=oauth.binding,
        )
        try:
            return await self._complete_open_transaction(
                principal=principal,
                oauth=oauth,
                transaction=transaction,
                session_digest=session_digest,
                state=state,
                code=code,
            )
        finally:
            await self._retire(transaction)

    async def _complete_open_transaction(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        oauth: ReviewerOAuthTransaction,
        transaction: RepositoryAttestationTransaction,
        session_digest: bytes | None,
        state: str | None,
        code: str,
    ) -> RepositoryAttestationCallbackOutcome:
        if (
            session_digest is None
            or not self._crypto.state_matches(oauth, state)
            or not hmac.compare_digest(oauth.binding.session_handle_digest, session_digest)
            or oauth.binding.initiating_actor != principal.actor_id
            or oauth.binding.authority_profile_digest != self._authority_profile_digest
            or principal.authority_profile_digest != self._authority_profile_digest
            or "configure" not in principal.roles
        ):
            return RepositoryAttestationCallbackOutcome("invalid")
        try:
            if not await self._store.attestation_is_pending(transaction):
                return RepositoryAttestationCallbackOutcome("replayed")
            now = await self._store.current_time()
        except ProposalReviewStoreUnavailable:
            return RepositoryAttestationCallbackOutcome("unavailable")
        if not oauth.binding.issued_at <= now < oauth.binding.expires_at:
            return RepositoryAttestationCallbackOutcome("replayed")
        try:
            identity = await self._provider.exchange_and_resolve(
                code=code,
                code_verifier=oauth.code_verifier,
                scope=oauth.binding.scope,
            )
            observed_at = await self._store.current_time()
        except GitHubReviewerRejected:
            return RepositoryAttestationCallbackOutcome("invalid")
        except (GitHubReviewerUnavailable, ProposalReviewStoreUnavailable):
            return RepositoryAttestationCallbackOutcome("unavailable")
        if not oauth.binding.issued_at <= observed_at < oauth.binding.expires_at:
            return RepositoryAttestationCallbackOutcome("replayed")
        reviewer = GitHubReviewerPrincipal(
            evidence=identity,
            observed_at=observed_at,
            expires_at=oauth.binding.expires_at,
        )
        command = ProposalReviewCommand(
            scope=oauth.binding.scope,
            operation_id=oauth.binding.operation_id,
            expected_manifest_id=oauth.binding.proposal_manifest_id,
            expected_active=(
                None
                if oauth.binding.expected_active_epoch_id is None
                else _active_pointer(oauth.binding)
            ),
            actor=oauth.binding.initiating_actor,
        )
        review = await ProposalReviewService(
            authorizer=_ExactAttestationAuthorizer(command),
            discovery=self._discovery,
            store=self._store,
            clock=self._clock,
        ).accept(
            command,
            attestation=RepositoryAttestationEvidence(
                transaction=transaction,
                reviewer=reviewer,
            ),
        )
        if review.state == "attestation_conflict":
            return RepositoryAttestationCallbackOutcome("replayed")
        return RepositoryAttestationCallbackOutcome("completed", review=review)

    async def _complete_registered_epoch(
        self,
        *,
        principal: KeycloakHumanPrincipal,
        oauth: RegisteredEpochReviewerOAuthTransaction,
        transaction: RegisteredEpochAttestationTransaction,
        session_digest: bytes | None,
        state: str | None,
        code: str,
    ) -> RepositoryAttestationCallbackOutcome:
        binding = oauth.binding
        if (
            session_digest is None
            or not self._crypto.state_matches(oauth, state)
            or not hmac.compare_digest(binding.session_handle_digest, session_digest)
            or binding.initiating_actor != principal.actor_id
            or binding.authority_profile_digest != self._authority_profile_digest
            or principal.authority_profile_digest != self._authority_profile_digest
            or "configure" not in principal.roles
        ):
            return RepositoryAttestationCallbackOutcome("invalid")
        store = self._registered_epoch_store
        try:
            if not await store.attestation_is_pending(transaction):
                return RepositoryAttestationCallbackOutcome("replayed")
            now = await store.current_time()
        except ProposalReviewStoreUnavailable:
            return RepositoryAttestationCallbackOutcome("unavailable")
        if not binding.issued_at <= now < binding.expires_at:
            return RepositoryAttestationCallbackOutcome("replayed")
        try:
            identity = await self._provider.exchange_and_resolve(
                code=code,
                code_verifier=oauth.code_verifier,
                scope=binding.scope,
            )
            observed_at = await store.current_time()
        except GitHubReviewerRejected:
            return RepositoryAttestationCallbackOutcome("invalid")
        except (GitHubReviewerUnavailable, ProposalReviewStoreUnavailable):
            return RepositoryAttestationCallbackOutcome("unavailable")
        if not binding.issued_at <= observed_at < binding.expires_at:
            return RepositoryAttestationCallbackOutcome("replayed")
        expected_active = (
            None
            if binding.expected_active_epoch_id is None or binding.expected_active_revision is None
            else ActiveConfigEpoch(
                binding.scope,
                binding.expected_active_epoch_id,
                binding.expected_active_revision,
            )
        )
        review = await RegisteredEpochReviewService(
            discovery=self._discovery,
            store=store,
            clock=self._clock,
        ).accept(
            RegisteredEpochReviewCommand(
                scope=binding.scope,
                operation_id=binding.operation_id,
                target_epoch_id=binding.target_epoch_id,
                expected_source_hash=binding.source_hash,
                expected_active=expected_active,
                actor=binding.initiating_actor,
            ),
            attestation=RegisteredEpochAttestationEvidence(
                transaction=transaction,
                reviewer=GitHubReviewerPrincipal(
                    evidence=identity,
                    observed_at=observed_at,
                    expires_at=binding.expires_at,
                ),
            ),
        )
        if review.state == "attestation_conflict":
            return RepositoryAttestationCallbackOutcome("replayed")
        return RepositoryAttestationCallbackOutcome("completed", registered_epoch_review=review)

    async def _retire_registered_epoch(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> None:
        try:
            await self._registered_epoch_store.retire_attestation(transaction)
        except ProposalReviewStoreUnavailable:
            # Expiry independently bounds a row when durable cleanup is unavailable.
            return

    async def _retire(self, transaction: RepositoryAttestationTransaction) -> None:
        try:
            await self._store.retire_attestation(transaction)
        except ProposalReviewStoreUnavailable:
            # Expiry independently bounds a row when durable cleanup is unavailable.
            return


@dataclass(frozen=True, slots=True)
class _ExactAttestationAuthorizer:
    command: ProposalReviewCommand

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return actor == self.command.actor and scope == self.command.scope


def _active_pointer(binding: ReviewerStepUpBinding) -> ActiveConfigEpoch:
    if binding.expected_active_epoch_id is None or binding.expected_active_revision is None:
        raise ValueError("repository-attestation active pointer is incomplete")
    return ActiveConfigEpoch(
        binding.scope,
        binding.expected_active_epoch_id,
        binding.expected_active_revision,
    )
