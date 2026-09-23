from __future__ import annotations

from typing import NoReturn

from ci_coordinator.audit_replay._event_contracts import (
    AuditEventError,
    AuditEventInput,
    JsonValue,
)
from ci_coordinator.audit_replay.subjects import AuditSubjectType

_PREPARED_AUDIT_EVENT_TOKEN = object()


class PreparedAuditEvent:
    __actor: str
    __consumed: bool
    __created_at: str
    __event_type: str
    __idempotency_key: str
    __input_hash: str
    __installation_id: int | None
    __payload_canonical_bytes: bytes
    __payload_hash: str
    __payload_snapshot: JsonValue
    __repository_id: int | None
    __subject_id: str
    __subject_type: AuditSubjectType

    __slots__ = (
        "__actor",
        "__consumed",
        "__created_at",
        "__event_type",
        "__idempotency_key",
        "__input_hash",
        "__installation_id",
        "__payload_canonical_bytes",
        "__payload_hash",
        "__payload_snapshot",
        "__repository_id",
        "__subject_id",
        "__subject_type",
    )

    def __init__(
        self,
        token: object,
        *,
        idempotency_key: str,
        installation_id: int | None = None,
        repository_id: int | None = None,
        subject_type: AuditSubjectType,
        subject_id: str,
        event_type: str,
        created_at: str,
        actor: str,
        payload_snapshot: JsonValue,
        payload_canonical_bytes: bytes,
        payload_hash: str,
        input_hash: str,
    ) -> None:
        if token is not _PREPARED_AUDIT_EVENT_TOKEN:
            raise TypeError("PreparedAuditEvent cannot be constructed directly")
        object.__setattr__(self, "_PreparedAuditEvent__idempotency_key", idempotency_key)
        object.__setattr__(self, "_PreparedAuditEvent__installation_id", installation_id)
        object.__setattr__(self, "_PreparedAuditEvent__repository_id", repository_id)
        object.__setattr__(self, "_PreparedAuditEvent__subject_type", subject_type)
        object.__setattr__(self, "_PreparedAuditEvent__subject_id", subject_id)
        object.__setattr__(self, "_PreparedAuditEvent__event_type", event_type)
        object.__setattr__(self, "_PreparedAuditEvent__created_at", created_at)
        object.__setattr__(self, "_PreparedAuditEvent__actor", actor)
        object.__setattr__(self, "_PreparedAuditEvent__consumed", False)
        object.__setattr__(self, "_PreparedAuditEvent__payload_snapshot", payload_snapshot)
        object.__setattr__(
            self,
            "_PreparedAuditEvent__payload_canonical_bytes",
            bytes(payload_canonical_bytes),
        )
        object.__setattr__(self, "_PreparedAuditEvent__payload_hash", payload_hash)
        object.__setattr__(self, "_PreparedAuditEvent__input_hash", input_hash)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("PreparedAuditEvent cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("PreparedAuditEvent is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("PreparedAuditEvent cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("PreparedAuditEvent cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("PreparedAuditEvent cannot be serialized")

    @property
    def idempotency_key(self) -> str:
        return self.__idempotency_key

    @property
    def installation_id(self) -> int | None:
        return self.__installation_id

    @property
    def repository_id(self) -> int | None:
        return self.__repository_id

    @property
    def subject_type(self) -> AuditSubjectType:
        return self.__subject_type

    @property
    def subject_id(self) -> str:
        return self.__subject_id

    @property
    def event_type(self) -> str:
        return self.__event_type

    @property
    def created_at(self) -> str:
        return self.__created_at

    @property
    def actor(self) -> str:
        return self.__actor

    @property
    def payload_canonical_bytes(self) -> bytes:
        return self.__payload_canonical_bytes

    @property
    def payload_hash(self) -> str:
        return self.__payload_hash

    @property
    def input_hash(self) -> str:
        return self.__input_hash

    def _take_payload_for_record(self) -> JsonValue:
        if self.__consumed:
            raise AuditEventError("prepared audit event has already been consumed")
        object.__setattr__(self, "_PreparedAuditEvent__consumed", True)
        return self.__payload_snapshot


def _make_prepared_audit_event(
    event_input: AuditEventInput,
    *,
    payload_snapshot: JsonValue,
    payload_canonical_bytes: bytes,
    payload_hash: str,
    input_hash: str,
) -> PreparedAuditEvent:
    return PreparedAuditEvent(
        _PREPARED_AUDIT_EVENT_TOKEN,
        idempotency_key=event_input.idempotency_key,
        installation_id=event_input.installation_id,
        repository_id=event_input.repository_id,
        subject_type=event_input.subject_type,
        subject_id=event_input.subject_id,
        event_type=event_input.event_type,
        created_at=event_input.created_at,
        actor=event_input.actor,
        payload_snapshot=payload_snapshot,
        payload_canonical_bytes=payload_canonical_bytes,
        payload_hash=payload_hash,
        input_hash=input_hash,
    )


def _require_exact_prepared_audit_event(prepared: object) -> None:
    if type(prepared) is not PreparedAuditEvent:
        raise AuditEventError("audit event must be an exact PreparedAuditEvent")


def _audit_event_input_from_prepared(prepared: PreparedAuditEvent) -> AuditEventInput:
    _require_exact_prepared_audit_event(prepared)
    return AuditEventInput(
        idempotency_key=prepared.idempotency_key,
        installation_id=prepared.installation_id,
        repository_id=prepared.repository_id,
        subject_type=prepared.subject_type,
        subject_id=prepared.subject_id,
        event_type=prepared.event_type,
        created_at=prepared.created_at,
        actor=prepared.actor,
        payload=_payload_from_prepared_audit_event(prepared),
    )


def _payload_from_prepared_audit_event(prepared: PreparedAuditEvent) -> JsonValue:
    _require_exact_prepared_audit_event(prepared)
    return prepared._take_payload_for_record()
