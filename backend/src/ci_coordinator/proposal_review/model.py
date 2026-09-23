"""Immutable workflow-proposal review command and result algebra."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import ActiveConfigEpoch
from ci_coordinator.proposal_review.attestation import RepositoryAttestationEvidence
from ci_coordinator.proposal_review.semantic_diff import PolicySemanticDiff

_MANIFEST_ID = re.compile(r"proposal:[0-9a-f]{32}")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_AUDIT_EVENT_ID = re.compile(r"audit_[0-9a-f]{32}")
_MAX_OPERATION_ID_BYTES = 256
_MAX_ACTOR_BYTES = 256


@dataclass(frozen=True, slots=True)
class ProposalReviewCommand:
    scope: RepositoryScope
    operation_id: str
    expected_manifest_id: str
    expected_active: ActiveConfigEpoch | None
    actor: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("proposal review scope must be exact")
        _require_bounded_text(self.operation_id, "operation id", _MAX_OPERATION_ID_BYTES)
        if "\0" in self.operation_id:
            raise ValueError("proposal review operation id cannot contain NUL")
        _require_bounded_text(self.actor, "actor", _MAX_ACTOR_BYTES)
        if (
            type(self.expected_manifest_id) is not str
            or _MANIFEST_ID.fullmatch(self.expected_manifest_id) is None
        ):
            raise ValueError("expected proposal manifest identity is invalid")
        if self.expected_active is not None and (
            type(self.expected_active) is not ActiveConfigEpoch
            or self.expected_active.scope != self.scope
        ):
            raise ValueError("expected active epoch must bind the review scope")


@dataclass(frozen=True, slots=True)
class ProposalReviewDraft:
    command: ProposalReviewCommand
    provider_revision: str
    inventory_digest: str
    proposal_digest: str
    target: ValidatedEpochDraft
    semantic_diff: PolicySemanticDiff

    def __post_init__(self) -> None:
        if type(self.command) is not ProposalReviewCommand:
            raise TypeError("proposal review draft requires an exact command")
        if (
            type(self.provider_revision) is not str
            or _SHA1.fullmatch(self.provider_revision) is None
        ):
            raise ValueError("proposal review provider revision is invalid")
        _require_sha256(self.inventory_digest, "proposal review inventory digest")
        _require_sha256(self.proposal_digest, "proposal review proposal digest")
        if type(self.target) is not ValidatedEpochDraft:
            raise TypeError("proposal review target must be an admitted epoch draft")
        if type(self.semantic_diff) is not PolicySemanticDiff:
            raise TypeError("proposal review semantic diff must be exact")
        expected_base = (
            None if self.command.expected_active is None else self.command.expected_active.epoch_id
        )
        if (
            self.target.scope != self.command.scope
            or self.semantic_diff.scope != self.command.scope
            or self.semantic_diff.base_epoch_id != expected_base
            or self.semantic_diff.target_epoch_id != self.target.epoch_id
        ):
            raise ValueError("proposal review draft crosses its scope or epoch bindings")


@dataclass(frozen=True, slots=True)
class ProposalReviewRecord:
    command: ProposalReviewCommand
    provider_revision: str
    inventory_digest: str
    proposal_digest: str
    target_epoch_id: str
    semantic_diff: PolicySemanticDiff
    attestation: RepositoryAttestationEvidence
    audit_event_id: str
    audit_input_hash: str

    @classmethod
    def from_draft(
        cls,
        draft: ProposalReviewDraft,
        *,
        attestation: RepositoryAttestationEvidence,
        audit_event_id: str,
        audit_input_hash: str,
    ) -> ProposalReviewRecord:
        return cls(
            command=draft.command,
            provider_revision=draft.provider_revision,
            inventory_digest=draft.inventory_digest,
            proposal_digest=draft.proposal_digest,
            target_epoch_id=draft.target.epoch_id,
            semantic_diff=draft.semantic_diff,
            attestation=attestation,
            audit_event_id=audit_event_id,
            audit_input_hash=audit_input_hash,
        )

    def __post_init__(self) -> None:
        if type(self.command) is not ProposalReviewCommand:
            raise TypeError("proposal review record requires an exact command")
        if (
            type(self.provider_revision) is not str
            or _SHA1.fullmatch(self.provider_revision) is None
        ):
            raise ValueError("proposal review record provider revision is invalid")
        _require_sha256(self.inventory_digest, "proposal review record inventory digest")
        _require_sha256(self.proposal_digest, "proposal review record proposal digest")
        _require_sha256(self.target_epoch_id, "proposal review target epoch")
        if type(self.attestation) is not RepositoryAttestationEvidence:
            raise TypeError("proposal review record requires exact repository attestation")
        binding = self.attestation.transaction.binding
        expected_active = self.command.expected_active
        if (
            binding.scope != self.command.scope
            or binding.operation_id != self.command.operation_id
            or binding.proposal_manifest_id != self.command.expected_manifest_id
            or binding.revision != self.provider_revision
            or binding.proposal_digest != self.proposal_digest
            or binding.initiating_actor != self.command.actor
            or binding.expected_active_epoch_id
            != (None if expected_active is None else expected_active.epoch_id)
            or binding.expected_active_revision
            != (None if expected_active is None else expected_active.revision)
        ):
            raise ValueError("proposal review record crosses its attestation binding")
        if (
            type(self.semantic_diff) is not PolicySemanticDiff
            or self.semantic_diff.scope != self.command.scope
            or self.semantic_diff.base_epoch_id
            != (
                None
                if self.command.expected_active is None
                else self.command.expected_active.epoch_id
            )
            or self.semantic_diff.target_epoch_id != self.target_epoch_id
        ):
            raise ValueError("proposal review record semantic diff is inconsistent")
        if (
            type(self.audit_event_id) is not str
            or _AUDIT_EVENT_ID.fullmatch(self.audit_event_id) is None
        ):
            raise ValueError("proposal review audit event identity is invalid")
        _require_sha256(self.audit_input_hash, "proposal review audit input hash")


@dataclass(frozen=True, slots=True)
class ProposalReviewCreated:
    record: ProposalReviewRecord
    epoch_created: bool

    def __post_init__(self) -> None:
        _require_record(self.record)
        if type(self.epoch_created) is not bool:
            raise TypeError("proposal review epoch-created fact must be boolean")


@dataclass(frozen=True, slots=True)
class ProposalReviewDuplicate:
    record: ProposalReviewRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class ProposalReviewBaselineConflict:
    active: ActiveConfigEpoch | None

    def __post_init__(self) -> None:
        if self.active is not None and type(self.active) is not ActiveConfigEpoch:
            raise TypeError("proposal review active conflict pointer must be exact")


@dataclass(frozen=True, slots=True)
class ProposalReviewOperationConflict:
    existing: ProposalReviewRecord

    def __post_init__(self) -> None:
        _require_record(self.existing)


@dataclass(frozen=True, slots=True)
class ProposalReviewEpochConflict:
    epoch_id: str

    def __post_init__(self) -> None:
        _require_sha256(self.epoch_id, "conflicting proposal review epoch")


@dataclass(frozen=True, slots=True)
class ProposalReviewAttestationConflict:
    pass


type ProposalReviewResolution = ProposalReviewDuplicate | ProposalReviewOperationConflict | None
type ProposalReviewWriteResult = (
    ProposalReviewCreated
    | ProposalReviewDuplicate
    | ProposalReviewBaselineConflict
    | ProposalReviewOperationConflict
    | ProposalReviewEpochConflict
    | ProposalReviewAttestationConflict
)


def _require_bounded_text(value: object, name: str, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"proposal review {name} must be bounded Unicode scalar text")


def _require_sha256(value: object, name: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def _require_record(value: object) -> None:
    if type(value) is not ProposalReviewRecord:
        raise TypeError("proposal review result requires an exact record")
