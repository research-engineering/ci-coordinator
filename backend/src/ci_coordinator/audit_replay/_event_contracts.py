from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.audit_replay.subjects import AuditSubjectType

AUDIT_EVENT_SCHEMA_VERSION: Final = "ci-audit-event/v1"

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]

type AuditJsonResourceErrorCode = Literal[
    "audit_payload_max_canonical_bytes_exceeded",
    "audit_text_max_utf8_bytes_exceeded",
    "json_max_depth_exceeded",
    "json_max_nodes_exceeded",
]


@dataclass(frozen=True)
class AuditJsonResourceFailure:
    code: AuditJsonResourceErrorCode
    instance_pointer: str
    limit: int
    observed: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "code": self.code,
            "instancePointer": self.instance_pointer,
            "limit": self.limit,
            "observed": self.observed,
        }


class AuditEventError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        audit_event_id: str | None = None,
        resource_failure: AuditJsonResourceFailure | None = None,
    ) -> None:
        super().__init__(message)
        self.audit_event_id = audit_event_id
        self.resource_failure = resource_failure


@dataclass(frozen=True)
class AuditEventInput:
    idempotency_key: str
    subject_type: AuditSubjectType
    subject_id: str
    event_type: str
    created_at: str
    actor: str
    payload: JsonValue
    installation_id: int | None = None
    repository_id: int | None = None


@dataclass(frozen=True)
class AuditEventRecord:
    idempotency_key: str
    subject_type: AuditSubjectType
    subject_id: str
    event_type: str
    created_at: str
    actor: str
    payload: JsonValue
    schema_version: Literal["ci-audit-event/v1"]
    audit_event_id: str
    sequence: int
    previous_event_hash: str | None
    payload_hash: str
    input_hash: str
    event_hash: str
    installation_id: int | None = None
    repository_id: int | None = None


type AuditEventRecordLike = AuditEventRecord | Mapping[str, object]
