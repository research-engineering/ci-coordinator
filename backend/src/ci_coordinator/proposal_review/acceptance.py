"""Single-use pair-owned audit capability for proposal acceptance."""

from __future__ import annotations

from typing import Final, NoReturn

from ci_coordinator.audit_replay import AuditEventInput, PreparedAuditEvent, prepare_audit_event
from ci_coordinator.proposal_review.attestation import RepositoryAttestationEvidence
from ci_coordinator.proposal_review.model import ProposalReviewDraft

PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE: Final = "workflow-proposal-accepted/v1"
_AUDIT_SCHEMA: Final = "workflow-proposal-acceptance-audit/v1"
_PREPARED_TOKEN = object()


class PreparedProposalReview:
    """Non-forgeable review and audit pair consumed by its persistence owner."""

    __audit_event: PreparedAuditEvent
    __attestation: RepositoryAttestationEvidence
    __consumed: bool
    __draft: ProposalReviewDraft

    __slots__ = ("__attestation", "__audit_event", "__consumed", "__draft")

    def __init__(
        self,
        token: object,
        *,
        draft: ProposalReviewDraft,
        attestation: RepositoryAttestationEvidence,
        audit_event: PreparedAuditEvent,
    ) -> None:
        if token is not _PREPARED_TOKEN:
            raise TypeError("PreparedProposalReview cannot be constructed directly")
        if (
            type(draft) is not ProposalReviewDraft
            or type(attestation) is not RepositoryAttestationEvidence
            or type(audit_event) is not PreparedAuditEvent
        ):
            raise TypeError("prepared proposal review pair is invalid")
        object.__setattr__(self, "_PreparedProposalReview__draft", draft)
        object.__setattr__(self, "_PreparedProposalReview__attestation", attestation)
        object.__setattr__(self, "_PreparedProposalReview__audit_event", audit_event)
        object.__setattr__(self, "_PreparedProposalReview__consumed", False)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedProposalReview cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PreparedProposalReview is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedProposalReview cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedProposalReview cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedProposalReview cannot be serialized")

    @property
    def draft(self) -> ProposalReviewDraft:
        return self.__draft

    @property
    def audit_input_hash(self) -> str:
        return self.__audit_event.input_hash

    @property
    def attestation(self) -> RepositoryAttestationEvidence:
        return self.__attestation

    def _take_audit_event(self) -> PreparedAuditEvent:
        if self.__consumed:
            raise ValueError("prepared proposal review audit capability was already consumed")
        object.__setattr__(self, "_PreparedProposalReview__consumed", True)
        return self.__audit_event


def prepare_proposal_review(
    draft: ProposalReviewDraft,
    *,
    attestation: RepositoryAttestationEvidence,
    occurred_at: str,
) -> PreparedProposalReview:
    if type(draft) is not ProposalReviewDraft:
        raise TypeError("proposal review preparation requires an exact draft")
    if type(attestation) is not RepositoryAttestationEvidence:
        raise TypeError("proposal review preparation requires exact repository attestation")
    binding = attestation.transaction.binding
    command = draft.command
    scope = command.scope
    active = command.expected_active
    if (
        binding.scope != scope
        or binding.operation_id != command.operation_id
        or binding.proposal_manifest_id != command.expected_manifest_id
        or binding.revision != draft.provider_revision
        or binding.proposal_digest != draft.proposal_digest
        or binding.initiating_actor != command.actor
        or binding.expected_active_epoch_id != (None if active is None else active.epoch_id)
        or binding.expected_active_revision != (None if active is None else active.revision)
    ):
        raise ValueError("proposal review preparation crosses its attestation binding")
    reviewer = attestation.reviewer
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=(
                f"workflow-proposal-review:{scope.installation_id}:"
                f"{scope.repository_id}:{command.operation_id}"
            ),
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            subject_type="policy-decision",
            subject_id=command.expected_manifest_id,
            event_type=PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE,
            created_at=occurred_at,
            actor=command.actor,
            payload={
                "schemaVersion": _AUDIT_SCHEMA,
                "operationId": command.operation_id,
                "proposalManifestId": command.expected_manifest_id,
                "providerRevision": draft.provider_revision,
                "inventoryDigest": draft.inventory_digest,
                "proposalDigest": draft.proposal_digest,
                "baseEpochId": None if active is None else active.epoch_id,
                "baseRevision": None if active is None else active.revision,
                "targetEpochId": draft.target.epoch_id,
                "semanticDiffVersion": draft.semantic_diff.version,
                "semanticDiffHash": draft.semantic_diff.diff_hash,
                "changedPointerCount": len(draft.semantic_diff.changed_pointers),
                "initiatingActor": binding.initiating_actor,
                "reviewerActor": reviewer.actor_id,
                "reviewerUserId": reviewer.user_id,
                "reviewerLogin": reviewer.login,
                "reviewerPermission": reviewer.permission,
                "attestationIssuedAt": binding.issued_at.isoformat(),
                "permissionObservedAt": reviewer.observed_at.isoformat(),
                "receiptExpiresAt": reviewer.expires_at.isoformat(),
                "authorityProfileDigest": binding.authority_profile_digest,
                "sessionHandleDigest": binding.session_handle_digest.hex(),
                "attestationTransactionDigest": attestation.transaction.transaction_digest.hex(),
            },
        )
    )
    return PreparedProposalReview(
        _PREPARED_TOKEN,
        draft=draft,
        attestation=attestation,
        audit_event=event,
    )


def is_pair_owned_proposal_review_event_type(value: object) -> bool:
    return value == PROPOSAL_REVIEW_ACCEPTED_EVENT_TYPE
