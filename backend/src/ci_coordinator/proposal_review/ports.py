"""Durable capabilities required by proposal-review orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot, PreparedConfigEpochActivation
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence
from ci_coordinator.proposal_review.acceptance import PreparedProposalReview
from ci_coordinator.proposal_review.activation import (
    AttestedConfigActivationResult,
    RepositoryActivationAuthority,
)
from ci_coordinator.proposal_review.attestation import (
    RepositoryAttestationRegistration,
    RepositoryAttestationTransaction,
)
from ci_coordinator.proposal_review.model import (
    ProposalReviewCommand,
    ProposalReviewRecord,
    ProposalReviewResolution,
    ProposalReviewWriteResult,
)


class ProposalReviewStoreUnavailable(RuntimeError):
    """The configured durable review store cannot complete the operation."""


class GitHubReviewerRejected(RuntimeError):
    """The provider rejected or could not prove the requested reviewer evidence."""


class GitHubReviewerUnavailable(RuntimeError):
    """The provider could not complete a reviewer-evidence operation."""


class ProposalReviewStore(Protocol):
    async def current_time(self) -> datetime: ...

    async def register_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> RepositoryAttestationRegistration: ...

    async def attestation_is_pending(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> bool: ...

    async def retire_attestation(
        self,
        transaction: RepositoryAttestationTransaction,
    ) -> None: ...

    async def resolve_operation(
        self,
        command: ProposalReviewCommand,
    ) -> ProposalReviewResolution: ...

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None: ...

    async def accept(self, prepared: PreparedProposalReview) -> ProposalReviewWriteResult: ...

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> ProposalReviewRecord | None: ...

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RepositoryActivationAuthority,
    ) -> AttestedConfigActivationResult: ...


class RepositoryActivationStore(Protocol):
    async def current_time(self) -> datetime: ...

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> ProposalReviewRecord | None: ...

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None: ...

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RepositoryActivationAuthority,
    ) -> AttestedConfigActivationResult: ...


class GitHubReviewerProvider(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange_and_resolve(
        self,
        *,
        code: str,
        code_verifier: str,
        scope: RepositoryScope,
    ) -> GitHubReviewerEvidence: ...


class GitHubReviewerPermissionReader(Protocol):
    async def recheck(
        self,
        *,
        scope: RepositoryScope,
        reviewer_user_id: int,
        reviewer_login: str,
    ) -> GitHubReviewerEvidence: ...
