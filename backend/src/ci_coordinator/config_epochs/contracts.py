"""Pure command and result contracts for immutable config epoch lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, NoReturn, cast

from ci_coordinator.audit_replay import AuditEventInput, PreparedAuditEvent, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_EPOCH_ID_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_MANIFEST_ID_PATTERN: Final = re.compile(r"^proposal:[0-9a-f]{32}$")
MAX_CONFIG_OPERATION_ID_UTF8_BYTES: Final = 256
MAX_ROLLBACK_REASON_UTF8_BYTES: Final = 512
_ACTIVATION_EVENT_TYPE: Final = "config-epoch-activation/v1"
_ACTIVATION_AUDIT_SCHEMA: Final = "config-epoch-activation-audit/v1"
_ROLLBACK_EVENT_TYPE: Final = "config-epoch-rollback/v1"
_ROLLBACK_AUDIT_SCHEMA: Final = "config-epoch-rollback-audit/v1"
_PAIR_OWNED_EVENT_TYPES: Final = frozenset({_ACTIVATION_EVENT_TYPE, _ROLLBACK_EVENT_TYPE})
_PREPARED_ACTIVATION_TOKEN = object()

type ConfigEpochMutationKind = Literal["activation", "rollback"]
type ProvenCoverageRelation = Literal["equal", "greater"]


@dataclass(frozen=True, slots=True)
class ActiveConfigEpoch:
    scope: RepositoryScope
    epoch_id: str
    revision: int

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_epoch_id(self.epoch_id)
        _require_positive_safe_integer(self.revision, "revision")


@dataclass(frozen=True, slots=True)
class ActiveConfigEpochSnapshot:
    active: ActiveConfigEpoch
    draft: ValidatedEpochDraft

    def __post_init__(self) -> None:
        if type(self.draft) is not ValidatedEpochDraft:
            raise ValueError("active config epoch snapshot requires an exact epoch draft")
        if self.draft.scope != self.active.scope or self.draft.epoch_id != self.active.epoch_id:
            raise ValueError("active config epoch snapshot pointer does not match its draft")


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationCommand:
    scope: RepositoryScope
    target_epoch_id: str
    expected_revision: int | None
    operation_id: str
    actor: str
    occurred_at: str
    proposal_manifest_id: str | None = None
    authority_evidence_hash: str | None = None
    authority_observed_at: str | None = None
    mutation_kind: ConfigEpochMutationKind = "activation"
    expected_active_epoch_id: str | None = None
    coverage_relation: ProvenCoverageRelation | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_epoch_id(self.target_epoch_id)
        if self.expected_revision is not None:
            _require_positive_safe_integer(self.expected_revision, "expected_revision")
        _require_bounded_text(
            self.operation_id,
            "operation_id",
            maximum_utf8_bytes=MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        )
        _require_nonempty_scalar_text(self.actor, "actor")
        _require_nonempty_scalar_text(self.occurred_at, "occurred_at")
        if self.mutation_kind == "activation":
            _require_proposal_manifest_id(self.proposal_manifest_id)
            _require_epoch_id(self.authority_evidence_hash)
            _require_nonempty_scalar_text(
                self.authority_observed_at,
                "authority_observed_at",
            )
            if any(
                value is not None
                for value in (
                    self.expected_active_epoch_id,
                    self.coverage_relation,
                    self.reason,
                )
            ):
                raise ValueError("ordinary activation cannot carry rollback evidence")
            return
        if self.mutation_kind != "rollback":
            raise ValueError("config epoch mutation kind is not admitted")
        if any(
            value is not None
            for value in (
                self.proposal_manifest_id,
                self.authority_evidence_hash,
                self.authority_observed_at,
            )
        ):
            raise ValueError("rollback cannot carry proposal activation authority")
        if self.expected_revision is None:
            raise ValueError("rollback requires an expected revision")
        _require_epoch_id(self.expected_active_epoch_id)
        if self.coverage_relation != "equal" and self.coverage_relation != "greater":
            raise ValueError("rollback requires a proven coverage relation")
        validate_rollback_reason(self.reason)


@dataclass(frozen=True, slots=True)
class ConfigEpochReplayCommand:
    """Client-owned operation facts sufficient for durable replay classification."""

    scope: RepositoryScope
    target_epoch_id: str
    expected_revision: int | None
    operation_id: str
    actor: str
    proposal_manifest_id: str | None = None
    mutation_kind: ConfigEpochMutationKind = "activation"
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_epoch_id(self.target_epoch_id)
        if self.expected_revision is not None:
            _require_positive_safe_integer(self.expected_revision, "expected_revision")
        _require_bounded_text(
            self.operation_id,
            "operation_id",
            maximum_utf8_bytes=MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        )
        _require_nonempty_scalar_text(self.actor, "actor")
        if self.mutation_kind == "activation":
            _require_proposal_manifest_id(self.proposal_manifest_id)
            if self.reason is not None:
                raise ValueError("ordinary activation cannot carry a rollback reason")
            return
        if self.mutation_kind != "rollback":
            raise ValueError("config epoch mutation kind is not admitted")
        if self.proposal_manifest_id is not None:
            raise ValueError("rollback cannot carry a proposal manifest")
        if self.expected_revision is None:
            raise ValueError("rollback requires an expected revision")
        validate_rollback_reason(self.reason)

    @classmethod
    def from_activation(cls, command: ConfigEpochActivationCommand) -> ConfigEpochReplayCommand:
        if type(command) is not ConfigEpochActivationCommand:
            raise TypeError("config epoch replay requires an exact activation command")
        return cls(
            scope=command.scope,
            target_epoch_id=command.target_epoch_id,
            expected_revision=command.expected_revision,
            operation_id=command.operation_id,
            actor=command.actor,
            proposal_manifest_id=command.proposal_manifest_id,
            mutation_kind=command.mutation_kind,
            reason=command.reason,
        )


class PreparedConfigEpochActivation:
    """Single-use pair-owned audit capability for one exact pointer mutation."""

    __audit_event: PreparedAuditEvent
    __command: ConfigEpochActivationCommand
    __consumed: bool

    __slots__ = ("__audit_event", "__command", "__consumed")

    def __init__(
        self,
        token: object,
        *,
        command: ConfigEpochActivationCommand,
        audit_event: PreparedAuditEvent,
    ) -> None:
        if token is not _PREPARED_ACTIVATION_TOKEN:
            raise TypeError("PreparedConfigEpochActivation cannot be constructed directly")
        object.__setattr__(self, "_PreparedConfigEpochActivation__command", command)
        object.__setattr__(self, "_PreparedConfigEpochActivation__audit_event", audit_event)
        object.__setattr__(self, "_PreparedConfigEpochActivation__consumed", False)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedConfigEpochActivation cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PreparedConfigEpochActivation is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedConfigEpochActivation cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedConfigEpochActivation cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedConfigEpochActivation cannot be serialized")

    @property
    def command(self) -> ConfigEpochActivationCommand:
        return self.__command

    @property
    def audit_input_hash(self) -> str:
        return self.__audit_event.input_hash

    def _take_audit_event(self) -> PreparedAuditEvent:
        if self.__consumed:
            raise RuntimeError("prepared config epoch activation has already been consumed")
        object.__setattr__(self, "_PreparedConfigEpochActivation__consumed", True)
        return self.__audit_event


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationRecord:
    scope: RepositoryScope
    operation_id: str
    expected_revision: int | None
    previous: ActiveConfigEpoch | None
    active: ActiveConfigEpoch
    audit_event_id: str
    audit_input_hash: str

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_bounded_text(self.operation_id, "operation_id")
        if self.expected_revision is not None:
            _require_positive_safe_integer(self.expected_revision, "expected_revision")
        if self.previous is not None and self.previous.scope != self.scope:
            raise ValueError("previous activation scope must match")
        if self.active.scope != self.scope:
            raise ValueError("active activation scope must match")
        if self.previous is None:
            if self.expected_revision is not None or self.active.revision != 1:
                raise ValueError(
                    "initial activation must create revision one from no expected revision"
                )
        elif (
            self.expected_revision != self.previous.revision
            or self.active.revision != self.previous.revision + 1
        ):
            raise ValueError("successor activation revisions are inconsistent")
        _require_audit_event_id(self.audit_event_id)
        _require_epoch_id(self.audit_input_hash)


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationCreated:
    epoch_id: str

    def __post_init__(self) -> None:
        _require_epoch_id(self.epoch_id)


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationDuplicate:
    epoch_id: str

    def __post_init__(self) -> None:
        _require_epoch_id(self.epoch_id)


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationConflict:
    epoch_id: str

    def __post_init__(self) -> None:
        _require_epoch_id(self.epoch_id)


type ConfigEpochRegistrationResult = (
    ConfigEpochRegistrationCreated
    | ConfigEpochRegistrationDuplicate
    | ConfigEpochRegistrationConflict
)


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationApplied:
    record: ConfigEpochActivationRecord


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationDuplicate:
    record: ConfigEpochActivationRecord


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationRevisionConflict:
    active: ActiveConfigEpoch | None


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationTargetUnavailable:
    pass


@dataclass(frozen=True, slots=True)
class ConfigEpochActivationOperationConflict:
    existing: ConfigEpochActivationRecord


type ConfigEpochActivationResult = (
    ConfigEpochActivationApplied
    | ConfigEpochActivationDuplicate
    | ConfigEpochActivationRevisionConflict
    | ConfigEpochActivationTargetUnavailable
    | ConfigEpochActivationOperationConflict
)
type ConfigEpochOperationResolution = (
    ConfigEpochActivationDuplicate | ConfigEpochActivationOperationConflict | None
)


def prepare_config_epoch_activation(
    command: ConfigEpochActivationCommand,
) -> PreparedConfigEpochActivation:
    """Bind one command to a pair-owned immutable audit input before database entry."""
    if type(command) is not ConfigEpochActivationCommand:
        raise TypeError("config epoch activation requires an exact command")
    scope = command.scope
    idempotency_prefix, event_type, payload = _audit_projection(command)
    audit_event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=(
                f"{idempotency_prefix}:{scope.installation_id}:"
                f"{scope.repository_id}:{command.operation_id}"
            ),
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            subject_type="policy-decision",
            subject_id=_audit_subject_id(scope),
            event_type=event_type,
            created_at=command.occurred_at,
            actor=command.actor,
            payload=payload,
        )
    )
    return PreparedConfigEpochActivation(
        _PREPARED_ACTIVATION_TOKEN,
        command=command,
        audit_event=audit_event,
    )


def is_pair_owned_audit_event_type(value: object) -> bool:
    return type(value) is str and value in _PAIR_OWNED_EVENT_TYPES


def validate_rollback_reason(value: object) -> str:
    """Return one rollback reason admitted by every config-epoch boundary."""
    _require_bounded_text(
        value,
        "reason",
        maximum_utf8_bytes=MAX_ROLLBACK_REASON_UTF8_BYTES,
    )
    return cast(str, value)


def _audit_projection(
    command: ConfigEpochActivationCommand,
) -> tuple[str, str, JsonValue]:
    if command.mutation_kind == "activation":
        return (
            "config-epoch-activation",
            _ACTIVATION_EVENT_TYPE,
            {
                "schemaVersion": _ACTIVATION_AUDIT_SCHEMA,
                "operationId": command.operation_id,
                "expectedRevision": command.expected_revision,
                "targetEpochId": command.target_epoch_id,
                "proposalManifestId": command.proposal_manifest_id,
                "authorityEvidenceHash": command.authority_evidence_hash,
                "authorityObservedAt": command.authority_observed_at,
            },
        )
    expected_active_epoch_id = cast(str, command.expected_active_epoch_id)
    coverage_relation = cast(ProvenCoverageRelation, command.coverage_relation)
    reason = cast(str, command.reason)
    return (
        "config-epoch-rollback",
        _ROLLBACK_EVENT_TYPE,
        {
            "schemaVersion": _ROLLBACK_AUDIT_SCHEMA,
            "operationId": command.operation_id,
            "expectedRevision": command.expected_revision,
            "activeEpochId": expected_active_epoch_id,
            "targetEpochId": command.target_epoch_id,
            "coverageRelation": coverage_relation,
            "reason": reason,
        },
    )


def _audit_subject_id(scope: RepositoryScope) -> str:
    return f"config-epoch:{scope.installation_id}:{scope.repository_id}"


def _require_scope(value: object) -> None:
    if type(value) is not RepositoryScope:
        raise ValueError("scope must be an exact RepositoryScope")


def _require_epoch_id(value: object) -> None:
    if type(value) is not str or _EPOCH_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("epoch identity must be lowercase SHA-256 hexadecimal")


def _require_proposal_manifest_id(value: object) -> None:
    if type(value) is not str or _PROPOSAL_MANIFEST_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("config activation proposal manifest identity is invalid")


def _require_positive_safe_integer(value: object, field_name: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"{field_name} must be a positive JSON safe integer")


def _require_bounded_text(
    value: object,
    field_name: str,
    *,
    maximum_utf8_bytes: int = MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
) -> None:
    _require_nonempty_scalar_text(value, field_name)
    text = cast(str, value)
    if len(text.encode("utf-8")) > maximum_utf8_bytes:
        raise ValueError(f"{field_name} exceeds the UTF-8 byte limit")


def _require_nonempty_scalar_text(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or any(0xD800 <= ord(item) <= 0xDFFF for item in value)
    ):
        raise ValueError(f"{field_name} must be a non-empty Unicode scalar string")


def _require_audit_event_id(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"audit_[0-9a-f]{32}", value) is None:
        raise ValueError("audit_event_id is invalid")
