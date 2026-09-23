"""Application authorization for one repository config activation."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.proposal_review import (
    GitHubReviewerPermissionReader,
    GitHubReviewerRejected,
    GitHubReviewerUnavailable,
    ProposalReviewStoreUnavailable,
    RepositoryActivationAuthority,
    RepositoryActivationAuthorization,
    RepositoryActivationGranted,
    RepositoryActivationRejected,
    RepositoryActivationStore,
    RepositoryActivationUnavailable,
)
from ci_coordinator.workflow_discovery import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryUnavailable,
    WorkflowDiscoveryUseCase,
)


class RepositoryActivationAuthorityService:
    """Combine an unexpired review receipt with one fresh GitHub App observation."""

    def __init__(
        self,
        *,
        store: RepositoryActivationStore,
        permission_reader: GitHubReviewerPermissionReader,
        discovery: WorkflowDiscoveryUseCase,
        authority_profile_digest: str,
    ) -> None:
        if (
            type(authority_profile_digest) is not str
            or len(authority_profile_digest) != 64
            or any(character not in "0123456789abcdef" for character in authority_profile_digest)
        ):
            raise ValueError("repository activation authority profile is invalid")
        self._store = store
        self._permission_reader = permission_reader
        self._discovery = discovery
        self._authority_profile_digest = authority_profile_digest

    async def authorize(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        target_epoch_id: str,
        proposal_manifest_id: str,
    ) -> RepositoryActivationAuthorization:
        try:
            review = await self._store.load_activation_candidate(
                scope=scope,
                target_epoch_id=target_epoch_id,
                proposal_manifest_id=proposal_manifest_id,
            )
        except ProposalReviewStoreUnavailable:
            return RepositoryActivationUnavailable()
        if review is None:
            return RepositoryActivationRejected()
        retained = review.attestation
        if retained.transaction.binding.authority_profile_digest != self._authority_profile_digest:
            return RepositoryActivationRejected()
        try:
            active = await self._store.load_active(scope)
            if (None if active is None else active.active) != review.command.expected_active:
                return RepositoryActivationRejected()
            observed_at = await self._store.current_time()
        except ProposalReviewStoreUnavailable:
            return RepositoryActivationUnavailable()
        try:
            rechecked = await self._permission_reader.recheck(
                scope=scope,
                reviewer_user_id=retained.reviewer.user_id,
                reviewer_login=retained.reviewer.login,
            )
        except GitHubReviewerRejected:
            return RepositoryActivationRejected()
        except GitHubReviewerUnavailable:
            return RepositoryActivationUnavailable()
        discovered = await self._discovery(actor=actor, scope=scope, revision=None)
        if isinstance(discovered, WorkflowDiscoveryForbidden):
            return RepositoryActivationRejected()
        if isinstance(discovered, WorkflowDiscoveryUnavailable):
            return RepositoryActivationUnavailable()
        if not isinstance(discovered, WorkflowDiscoveryCompleted):
            raise RuntimeError("workflow discovery outcome algebra is incomplete")
        proposal = discovered.proposal
        if (
            proposal.state != "reviewable"
            or proposal.manifest_id != proposal_manifest_id
            or proposal.proposal_digest != review.proposal_digest
            or discovered.report.revision != review.provider_revision
            or type(proposal.admission) is not ValidatedEpochDraft
            or proposal.admission.epoch_id != target_epoch_id
        ):
            return RepositoryActivationRejected()
        try:
            authority = RepositoryActivationAuthority(
                review=review,
                rechecked=rechecked,
                rechecked_at=observed_at,
            )
        except (TypeError, ValueError):
            return RepositoryActivationRejected()
        return RepositoryActivationGranted(authority)
