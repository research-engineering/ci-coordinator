"""Exact repository authority for one config-epoch activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from ci_coordinator.config_epochs import ConfigEpochActivationResult
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence
from ci_coordinator.kernel import canonical_json
from ci_coordinator.proposal_review.model import ProposalReviewRecord


@dataclass(frozen=True, slots=True)
class RepositoryActivationAuthority:
    """Bind one fresh App observation to one immutable proposal-review receipt."""

    review: ProposalReviewRecord
    rechecked: GitHubReviewerEvidence
    rechecked_at: datetime

    def __post_init__(self) -> None:
        if type(self.review) is not ProposalReviewRecord:
            raise TypeError("repository activation authority requires an exact review")
        if type(self.rechecked) is not GitHubReviewerEvidence:
            raise TypeError("repository activation authority requires exact App evidence")
        observed_at = _aware_utc(self.rechecked_at)
        retained = self.review.attestation.reviewer
        if self.rechecked != retained.evidence:
            raise ValueError("repository activation reviewer evidence changed")
        if not retained.observed_at <= observed_at < retained.expires_at:
            raise ValueError("repository activation recheck is outside the review receipt")
        object.__setattr__(self, "rechecked_at", observed_at)

    @property
    def evidence_hash(self) -> str:
        review = self.review
        attestation = review.attestation
        binding = attestation.transaction.binding
        reviewer = attestation.reviewer
        active = review.command.expected_active
        projection = {
            "schemaVersion": "repository-activation-authority/v1",
            "installationId": review.command.scope.installation_id,
            "repositoryId": review.command.scope.repository_id,
            "targetEpochId": review.target_epoch_id,
            "reviewOperationId": review.command.operation_id,
            "proposalManifestId": review.command.expected_manifest_id,
            "providerRevision": review.provider_revision,
            "inventoryDigest": review.inventory_digest,
            "proposalDigest": review.proposal_digest,
            "baseEpochId": None if active is None else active.epoch_id,
            "baseRevision": None if active is None else active.revision,
            "semanticDiffVersion": review.semantic_diff.version,
            "semanticDiffHash": review.semantic_diff.diff_hash,
            "changedPointerCount": len(review.semantic_diff.changed_pointers),
            "attestationTransactionDigest": attestation.transaction.transaction_digest.hex(),
            "sessionHandleDigest": binding.session_handle_digest.hex(),
            "initiatingActor": binding.initiating_actor,
            "reviewerActor": reviewer.actor_id,
            "reviewerUserId": reviewer.user_id,
            "reviewerLogin": reviewer.login,
            "reviewerPermission": reviewer.permission,
            "attestationIssuedAt": _timestamp(binding.issued_at),
            "permissionObservedAt": _timestamp(reviewer.observed_at),
            "receiptExpiresAt": _timestamp(reviewer.expires_at),
            "authorityProfileDigest": binding.authority_profile_digest,
            "auditEventId": review.audit_event_id,
            "auditInputHash": review.audit_input_hash,
            "appRecheckedAt": _timestamp(self.rechecked_at),
        }
        return sha256(
            b"repository-activation-authority/v1\0" + canonical_json(projection)
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RepositoryActivationGranted:
    authority: RepositoryActivationAuthority

    def __post_init__(self) -> None:
        if type(self.authority) is not RepositoryActivationAuthority:
            raise TypeError("repository activation grant requires exact authority")


@dataclass(frozen=True, slots=True)
class RepositoryActivationRejected:
    pass


@dataclass(frozen=True, slots=True)
class RepositoryActivationUnavailable:
    pass


@dataclass(frozen=True, slots=True)
class RepositoryActivationAuthorityConflict:
    pass


type RepositoryActivationAuthorization = (
    RepositoryActivationGranted | RepositoryActivationRejected | RepositoryActivationUnavailable
)
type AttestedConfigActivationResult = (
    ConfigEpochActivationResult | RepositoryActivationAuthorityConflict
)


def _aware_utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("repository activation recheck must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
