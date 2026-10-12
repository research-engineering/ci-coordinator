"""Attested review algebra for one already registered, retained config epoch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, NoReturn

from ci_coordinator.audit_replay import AuditEventInput, PreparedAuditEvent, prepare_audit_event
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import ActiveConfigEpoch
from ci_coordinator.proposal_review.attestation import RegisteredEpochAttestationEvidence
from ci_coordinator.proposal_review.semantic_diff import PolicySemanticDiff

REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE: Final = "registered-epoch-review-accepted/v1"
_AUDIT_SCHEMA: Final = "registered-epoch-review-acceptance-audit/v1"
_SHA1: Final = re.compile(r"[0-9a-f]{40}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_AUDIT_EVENT_ID: Final = re.compile(r"audit_[0-9a-f]{32}")
_MAX_OPERATION_ID_BYTES: Final = 256
_MAX_ACTOR_BYTES: Final = 256
_PREPARED_TOKEN = object()


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewCommand:
    scope: RepositoryScope
    operation_id: str
    target_epoch_id: str
    expected_source_hash: str
    expected_active: ActiveConfigEpoch | None
    actor: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("registered-epoch review scope must be exact")
        _require_bounded_text(self.operation_id, "operation id", _MAX_OPERATION_ID_BYTES)
        if "\0" in self.operation_id:
            raise ValueError("registered-epoch review operation id cannot contain NUL")
        _require_bounded_text(self.actor, "actor", _MAX_ACTOR_BYTES)
        _require_sha256(self.target_epoch_id, "registered-epoch review target epoch")
        _require_sha256(self.expected_source_hash, "registered-epoch review source hash")
        if self.expected_active is not None and (
            type(self.expected_active) is not ActiveConfigEpoch
            or self.expected_active.scope != self.scope
        ):
            raise ValueError("expected active epoch must bind the review scope")


@dataclass(frozen=True, slots=True)
class RegisteredEpochRegistrant:
    """The earliest standalone registration receipt retained for one epoch."""

    operation_id: str
    actor: str
    audit_event_id: str

    def __post_init__(self) -> None:
        _require_bounded_text(self.operation_id, "registration operation id", 256)
        _require_bounded_text(self.actor, "registrant actor", _MAX_ACTOR_BYTES)
        if (
            type(self.audit_event_id) is not str
            or _AUDIT_EVENT_ID.fullmatch(self.audit_event_id) is None
        ):
            raise ValueError("registration audit event identity is invalid")


@dataclass(frozen=True, slots=True)
class RegisteredEpochSubject:
    """A re-admitted retained epoch together with its first standalone registrant."""

    draft: ValidatedEpochDraft
    registrant: RegisteredEpochRegistrant

    def __post_init__(self) -> None:
        if type(self.draft) is not ValidatedEpochDraft:
            raise TypeError("registered-epoch subject requires an admitted epoch draft")
        if type(self.registrant) is not RegisteredEpochRegistrant:
            raise TypeError("registered-epoch subject requires an exact registrant")


@dataclass(frozen=True, slots=True)
class RegisteredEpochWithoutReceipt:
    """The in-scope epoch exists but no standalone registration receipt retains it."""


type RegisteredEpochLookup = RegisteredEpochSubject | RegisteredEpochWithoutReceipt | None


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewDraft:
    command: RegisteredEpochReviewCommand
    provider_revision: str
    inventory_digest: str
    subject: RegisteredEpochSubject
    semantic_diff: PolicySemanticDiff

    def __post_init__(self) -> None:
        if type(self.command) is not RegisteredEpochReviewCommand:
            raise TypeError("registered-epoch review draft requires an exact command")
        _require_sha1(self.provider_revision, "registered-epoch review provider revision")
        _require_sha256(self.inventory_digest, "registered-epoch review inventory digest")
        if type(self.subject) is not RegisteredEpochSubject:
            raise TypeError("registered-epoch review draft requires an exact subject")
        if type(self.semantic_diff) is not PolicySemanticDiff:
            raise TypeError("registered-epoch review semantic diff must be exact")
        target = self.subject.draft
        _require_diff_binding(self.command, self.semantic_diff)
        if (
            target.scope != self.command.scope
            or target.epoch_id != self.command.target_epoch_id
            or target.source_hash != self.command.expected_source_hash
        ):
            raise ValueError("registered-epoch review draft crosses its target binding")


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewRecord:
    command: RegisteredEpochReviewCommand
    provider_revision: str
    inventory_digest: str
    registrant: RegisteredEpochRegistrant
    semantic_diff: PolicySemanticDiff
    attestation: RegisteredEpochAttestationEvidence
    audit_event_id: str
    audit_input_hash: str

    @classmethod
    def from_draft(
        cls,
        draft: RegisteredEpochReviewDraft,
        *,
        attestation: RegisteredEpochAttestationEvidence,
        audit_event_id: str,
        audit_input_hash: str,
    ) -> RegisteredEpochReviewRecord:
        return cls(
            command=draft.command,
            provider_revision=draft.provider_revision,
            inventory_digest=draft.inventory_digest,
            registrant=draft.subject.registrant,
            semantic_diff=draft.semantic_diff,
            attestation=attestation,
            audit_event_id=audit_event_id,
            audit_input_hash=audit_input_hash,
        )

    def __post_init__(self) -> None:
        if type(self.command) is not RegisteredEpochReviewCommand:
            raise TypeError("registered-epoch review record requires an exact command")
        _require_sha1(self.provider_revision, "registered-epoch review record provider revision")
        _require_sha256(self.inventory_digest, "registered-epoch review record inventory digest")
        if type(self.registrant) is not RegisteredEpochRegistrant:
            raise TypeError("registered-epoch review record requires an exact registrant")
        if type(self.semantic_diff) is not PolicySemanticDiff:
            raise TypeError("registered-epoch review record semantic diff must be exact")
        _require_diff_binding(self.command, self.semantic_diff)
        if type(self.attestation) is not RegisteredEpochAttestationEvidence:
            raise TypeError("registered-epoch review record requires exact attestation")
        _require_attestation_binding(
            self.command,
            self.attestation,
            provider_revision=self.provider_revision,
            inventory_digest=self.inventory_digest,
        )
        if (
            type(self.audit_event_id) is not str
            or _AUDIT_EVENT_ID.fullmatch(self.audit_event_id) is None
        ):
            raise ValueError("registered-epoch review audit event identity is invalid")
        _require_sha256(self.audit_input_hash, "registered-epoch review audit input hash")


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewCreated:
    record: RegisteredEpochReviewRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewDuplicate:
    record: RegisteredEpochReviewRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewOperationConflict:
    existing: RegisteredEpochReviewRecord

    def __post_init__(self) -> None:
        _require_record(self.existing)


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewBaselineConflict:
    active: ActiveConfigEpoch | None

    def __post_init__(self) -> None:
        if self.active is not None and type(self.active) is not ActiveConfigEpoch:
            raise TypeError("registered-epoch review active conflict pointer must be exact")


@dataclass(frozen=True, slots=True)
class RegisteredEpochReviewAttestationConflict:
    pass


type RegisteredEpochReviewResolution = (
    RegisteredEpochReviewDuplicate | RegisteredEpochReviewOperationConflict | None
)
type RegisteredEpochReviewWriteResult = (
    RegisteredEpochReviewCreated
    | RegisteredEpochReviewDuplicate
    | RegisteredEpochReviewOperationConflict
    | RegisteredEpochReviewBaselineConflict
    | RegisteredEpochReviewAttestationConflict
)


class PreparedRegisteredEpochReview:
    """Non-forgeable registered-epoch review and audit pair consumed by persistence."""

    __attestation: RegisteredEpochAttestationEvidence
    __audit_event: PreparedAuditEvent
    __consumed: bool
    __draft: RegisteredEpochReviewDraft

    __slots__ = ("__attestation", "__audit_event", "__consumed", "__draft")

    def __init__(
        self,
        token: object,
        *,
        draft: RegisteredEpochReviewDraft,
        attestation: RegisteredEpochAttestationEvidence,
        audit_event: PreparedAuditEvent,
    ) -> None:
        if token is not _PREPARED_TOKEN:
            raise TypeError("PreparedRegisteredEpochReview cannot be constructed directly")
        if (
            type(draft) is not RegisteredEpochReviewDraft
            or type(attestation) is not RegisteredEpochAttestationEvidence
            or type(audit_event) is not PreparedAuditEvent
        ):
            raise TypeError("prepared registered-epoch review pair is invalid")
        object.__setattr__(self, "_PreparedRegisteredEpochReview__draft", draft)
        object.__setattr__(self, "_PreparedRegisteredEpochReview__attestation", attestation)
        object.__setattr__(self, "_PreparedRegisteredEpochReview__audit_event", audit_event)
        object.__setattr__(self, "_PreparedRegisteredEpochReview__consumed", False)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedRegisteredEpochReview cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PreparedRegisteredEpochReview is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedRegisteredEpochReview cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedRegisteredEpochReview cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedRegisteredEpochReview cannot be serialized")

    @property
    def draft(self) -> RegisteredEpochReviewDraft:
        return self.__draft

    @property
    def attestation(self) -> RegisteredEpochAttestationEvidence:
        return self.__attestation

    @property
    def audit_input_hash(self) -> str:
        return self.__audit_event.input_hash

    def _take_audit_event(self) -> PreparedAuditEvent:
        if self.__consumed:
            raise ValueError("prepared registered-epoch review audit capability was consumed")
        object.__setattr__(self, "_PreparedRegisteredEpochReview__consumed", True)
        return self.__audit_event


def prepare_registered_epoch_review(
    draft: RegisteredEpochReviewDraft,
    *,
    attestation: RegisteredEpochAttestationEvidence,
    occurred_at: str,
) -> PreparedRegisteredEpochReview:
    if type(draft) is not RegisteredEpochReviewDraft:
        raise TypeError("registered-epoch review preparation requires an exact draft")
    if type(attestation) is not RegisteredEpochAttestationEvidence:
        raise TypeError("registered-epoch review preparation requires exact attestation")
    command = draft.command
    _require_attestation_binding(
        command,
        attestation,
        provider_revision=draft.provider_revision,
        inventory_digest=draft.inventory_digest,
    )
    binding = attestation.transaction.binding
    reviewer = attestation.reviewer
    scope = command.scope
    active = command.expected_active
    registrant = draft.subject.registrant
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=(
                f"registered-epoch-review:{scope.installation_id}:"
                f"{scope.repository_id}:{command.operation_id}"
            ),
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            subject_type="policy-decision",
            subject_id=f"registered-epoch:{command.target_epoch_id}",
            event_type=REGISTERED_EPOCH_REVIEW_ACCEPTED_EVENT_TYPE,
            created_at=occurred_at,
            actor=command.actor,
            payload={
                "schemaVersion": _AUDIT_SCHEMA,
                "operationId": command.operation_id,
                "targetEpochId": command.target_epoch_id,
                "sourceHash": command.expected_source_hash,
                "registrationOperationId": registrant.operation_id,
                "registrantActor": registrant.actor,
                "registrationAuditEventId": registrant.audit_event_id,
                "providerRevision": draft.provider_revision,
                "inventoryDigest": draft.inventory_digest,
                "baseEpochId": None if active is None else active.epoch_id,
                "baseRevision": None if active is None else active.revision,
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
    return PreparedRegisteredEpochReview(
        _PREPARED_TOKEN,
        draft=draft,
        attestation=attestation,
        audit_event=event,
    )


def _require_attestation_binding(
    command: RegisteredEpochReviewCommand,
    attestation: RegisteredEpochAttestationEvidence,
    *,
    provider_revision: str,
    inventory_digest: str,
) -> None:
    binding = attestation.transaction.binding
    active = command.expected_active
    if (
        binding.scope != command.scope
        or binding.operation_id != command.operation_id
        or binding.target_epoch_id != command.target_epoch_id
        or binding.source_hash != command.expected_source_hash
        or binding.revision != provider_revision
        or binding.inventory_digest != inventory_digest
        or binding.initiating_actor != command.actor
        or binding.expected_active_epoch_id != (None if active is None else active.epoch_id)
        or binding.expected_active_revision != (None if active is None else active.revision)
    ):
        raise ValueError("registered-epoch review crosses its attestation binding")


def _require_diff_binding(
    command: RegisteredEpochReviewCommand,
    semantic_diff: PolicySemanticDiff,
) -> None:
    active = command.expected_active
    if (
        semantic_diff.scope != command.scope
        or semantic_diff.base_epoch_id != (None if active is None else active.epoch_id)
        or semantic_diff.target_epoch_id != command.target_epoch_id
    ):
        raise ValueError("registered-epoch review semantic diff crosses its bindings")


def _require_bounded_text(value: object, name: str, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"registered-epoch review {name} must be bounded Unicode scalar text")


def _require_sha1(value: object, name: str) -> None:
    if type(value) is not str or _SHA1.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def _require_sha256(value: object, name: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def _require_record(value: object) -> None:
    if type(value) is not RegisteredEpochReviewRecord:
        raise TypeError("registered-epoch review result requires an exact record")
