"""Operation-aware config epoch registration and pair-owned audit contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, NoReturn

from ci_coordinator.audit_replay import (
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
    AuditEventInput,
    PreparedAuditEvent,
    prepare_audit_event,
)
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs.contracts import MAX_CONFIG_OPERATION_ID_UTF8_BYTES

CONFIG_EPOCH_REGISTRATION_EVENT_TYPE: Final = "config-epoch-registration/v1"
_AUDIT_SCHEMA: Final = "config-epoch-registration-audit/v1"
_MAX_OCCURRED_AT_UTF8_BYTES: Final = 128
_PREPARED_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationCommand:
    draft: ValidatedEpochDraft
    operation_id: str
    actor: str
    occurred_at: str

    def __post_init__(self) -> None:
        if type(self.draft) is not ValidatedEpochDraft:
            raise TypeError("config registration requires an exact validated draft")
        _require_bounded_text(
            self.operation_id,
            "operation_id",
            MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        )
        _require_bounded_text(self.actor, "actor", MAX_AUDIT_TEXT_UTF8_BYTES_V1)
        _require_bounded_text(
            self.occurred_at,
            "occurred_at",
            _MAX_OCCURRED_AT_UTF8_BYTES,
        )


class PreparedConfigEpochRegistration:
    """Single-use capability pairing one exact registration with its audit event."""

    __audit_event: PreparedAuditEvent
    __command: ConfigEpochRegistrationCommand
    __consumed: bool

    __slots__ = ("__audit_event", "__command", "__consumed")

    def __init__(
        self,
        token: object,
        *,
        command: ConfigEpochRegistrationCommand,
        audit_event: PreparedAuditEvent,
    ) -> None:
        if token is not _PREPARED_TOKEN:
            raise TypeError("PreparedConfigEpochRegistration cannot be constructed directly")
        if (
            type(command) is not ConfigEpochRegistrationCommand
            or type(audit_event) is not PreparedAuditEvent
        ):
            raise TypeError("prepared config registration pair is invalid")
        object.__setattr__(self, "_PreparedConfigEpochRegistration__command", command)
        object.__setattr__(self, "_PreparedConfigEpochRegistration__audit_event", audit_event)
        object.__setattr__(self, "_PreparedConfigEpochRegistration__consumed", False)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedConfigEpochRegistration cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PreparedConfigEpochRegistration is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedConfigEpochRegistration cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedConfigEpochRegistration cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedConfigEpochRegistration cannot be serialized")

    @property
    def command(self) -> ConfigEpochRegistrationCommand:
        return self.__command

    @property
    def audit_input_hash(self) -> str:
        return self.__audit_event.input_hash

    def _take_audit_event(self) -> PreparedAuditEvent:
        if self.__consumed:
            raise RuntimeError("prepared config registration has already been consumed")
        object.__setattr__(self, "_PreparedConfigEpochRegistration__consumed", True)
        return self.__audit_event


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationRecord:
    scope: RepositoryScope
    operation_id: str
    epoch_id: str
    audit_event_id: str
    audit_input_hash: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("config registration receipt requires an exact repository scope")
        _require_bounded_text(
            self.operation_id,
            "operation_id",
            MAX_CONFIG_OPERATION_ID_UTF8_BYTES,
        )
        _require_sha256(self.epoch_id, "epoch_id")
        if re.fullmatch(r"audit_[0-9a-f]{32}", self.audit_event_id) is None:
            raise ValueError("config registration audit event identity is invalid")
        _require_sha256(self.audit_input_hash, "audit_input_hash")


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationCommitted:
    record: ConfigEpochRegistrationRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationReplay:
    record: ConfigEpochRegistrationRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class ConfigEpochRegistrationOperationConflict:
    existing: ConfigEpochRegistrationRecord

    def __post_init__(self) -> None:
        _require_record(self.existing)


type ConfigEpochRegistrationOperationResult = (
    ConfigEpochRegistrationCommitted
    | ConfigEpochRegistrationReplay
    | ConfigEpochRegistrationOperationConflict
)


def prepare_config_epoch_registration(
    command: ConfigEpochRegistrationCommand,
) -> PreparedConfigEpochRegistration:
    if type(command) is not ConfigEpochRegistrationCommand:
        raise TypeError("config epoch registration requires an exact command")
    draft = command.draft
    scope = draft.scope
    event = prepare_audit_event(
        AuditEventInput(
            idempotency_key=(
                f"config-epoch-registration:{scope.installation_id}:"
                f"{scope.repository_id}:{command.operation_id}"
            ),
            installation_id=scope.installation_id,
            repository_id=scope.repository_id,
            subject_type="policy-decision",
            subject_id=f"config-epoch:{scope.installation_id}:{scope.repository_id}",
            event_type=CONFIG_EPOCH_REGISTRATION_EVENT_TYPE,
            created_at=command.occurred_at,
            actor=command.actor,
            payload={
                "schemaVersion": _AUDIT_SCHEMA,
                "operationId": command.operation_id,
                "epochId": draft.epoch_id,
                "sourceFormat": draft.source_format,
                "sourceHash": draft.source_hash,
                "documentHash": draft.document_hash,
                "epochHash": draft.epoch_hash,
            },
        )
    )
    return PreparedConfigEpochRegistration(
        _PREPARED_TOKEN,
        command=command,
        audit_event=event,
    )


def is_pair_owned_config_registration_event_type(value: object) -> bool:
    return value == CONFIG_EPOCH_REGISTRATION_EVENT_TYPE


def _require_bounded_text(value: object, name: str, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} is not bounded Unicode scalar text")


def _require_sha256(value: object, name: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} is not lowercase SHA-256 hexadecimal")


def _require_record(value: object) -> None:
    if type(value) is not ConfigEpochRegistrationRecord:
        raise TypeError("config registration result requires an exact record")
