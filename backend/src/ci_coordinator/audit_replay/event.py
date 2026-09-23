from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from ci_coordinator.audit_replay._event_codec import (
    audit_event_from_mapping,
    audit_event_input_from_mapping,
    audit_event_to_mapping,
    snapshot_audit_event_input,
    snapshot_json_value,
)
from ci_coordinator.audit_replay._event_construction import (
    build_audit_event,
    build_prepared_audit_event,
)
from ci_coordinator.audit_replay._event_contracts import (
    AUDIT_EVENT_SCHEMA_VERSION,
    AuditEventError,
    AuditEventInput,
    AuditEventRecord,
    AuditJsonResourceFailure,
)
from ci_coordinator.audit_replay._event_identity import (
    audit_event_hash,
    audit_input_hash,
    audit_input_hash_for_record,
)
from ci_coordinator.audit_replay._event_validation import (
    is_hash,
    validate_audit_event_input,
    validate_audit_event_record,
)
from ci_coordinator.audit_replay._prepared_event import (
    PreparedAuditEvent,
)
from ci_coordinator.audit_replay.subjects import (
    AuditSubjectType,
    is_audit_subject_type,
)

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]
type AuditJsonResourceErrorCode = Literal[
    "audit_payload_max_canonical_bytes_exceeded",
    "audit_text_max_utf8_bytes_exceeded",
    "json_max_depth_exceeded",
    "json_max_nodes_exceeded",
]
type AuditEventRecordLike = AuditEventRecord | Mapping[str, object]

__all__ = [
    "AUDIT_EVENT_SCHEMA_VERSION",
    "AuditEventError",
    "AuditEventInput",
    "AuditEventRecord",
    "AuditEventRecordLike",
    "AuditJsonResourceErrorCode",
    "AuditJsonResourceFailure",
    "AuditSubjectType",
    "JsonPrimitive",
    "JsonValue",
    "PreparedAuditEvent",
    "audit_event_from_mapping",
    "audit_event_hash",
    "audit_event_input_from_mapping",
    "audit_event_to_mapping",
    "audit_input_hash",
    "audit_input_hash_for_record",
    "build_audit_event",
    "build_prepared_audit_event",
    "is_audit_subject_type",
    "is_hash",
    "snapshot_audit_event_input",
    "snapshot_json_value",
    "validate_audit_event_input",
    "validate_audit_event_record",
]
