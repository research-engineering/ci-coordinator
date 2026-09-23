"""Canonical state and audit codecs for durable operator overrides."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from ci_coordinator.audit_replay import (
    AuditEventInput,
    AuditEventRecord,
    PreparedAuditEvent,
    prepare_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.operator_controls.override import (
    OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
    ActiveOverride,
    OverrideAuditEvent,
    OverrideCommand,
    OverrideKind,
)
from ci_coordinator.persistence.canonical_row import (
    CanonicalRowCodecError,
    decode_canonical_object,
    encode_canonical_object,
    require_bounded_text,
    require_bytes,
    require_digest,
    require_exact_keys,
    semantic_hash,
)

_RECORD_MAXIMUM_BYTES = 8_192
_OVERRIDE_ID = re.compile(r"^override_[0-9a-f]{32}$")
_AUDIT_EVENT_ID = re.compile(r"^audit_[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class StoredOverrideRecord:
    override: ActiveOverride
    audit_event_id: str
    audit_input_hash: str


def _prepare_applied_event(
    override: ActiveOverride,
    event: OverrideAuditEvent,
) -> PreparedAuditEvent:
    if type(override) is not ActiveOverride or type(event) is not OverrideAuditEvent:
        raise ValueError("operator override audit pair must be exact")
    if event.command != override.command or event != OverrideAuditEvent.applied(override):
        raise ValueError("operator override audit event does not match its state")
    command = override.command
    return prepare_audit_event(
        AuditEventInput(
            idempotency_key=_scope_operation_key("operator-override", command),
            installation_id=command.scope.installation_id,
            repository_id=command.scope.repository_id,
            subject_type="policy-decision",
            subject_id=_audit_subject_id(command.scope),
            event_type=OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
            created_at=_audit_timestamp(event.occurred_at),
            actor=command.actor,
            payload={
                "schemaVersion": "operator-override-audit/v1",
                "overrideId": override.override_id,
                "outcome": "applied",
                "command": _audit_command_projection(command),
                "appliedAt": _audit_timestamp(override.applied_at),
            },
        )
    )


def _prepare_rejection_event(event: OverrideAuditEvent) -> PreparedAuditEvent:
    if type(event) is not OverrideAuditEvent:
        raise ValueError("operator override rejection audit event is invalid")
    if event.outcome == "expired":
        expected = OverrideAuditEvent.rejected(event.command, "expired", event.occurred_at)
    elif event.outcome == "unauthorized":
        expected = OverrideAuditEvent.rejected(event.command, "unauthorized", event.occurred_at)
    else:
        raise ValueError("operator override rejection audit event is invalid")
    if event != expected:
        raise ValueError("operator override rejection audit identity is invalid")
    command = event.command
    return prepare_audit_event(
        AuditEventInput(
            idempotency_key=_scope_operation_key("operator-override-rejection", command),
            installation_id=command.scope.installation_id,
            repository_id=command.scope.repository_id,
            subject_type="policy-decision",
            subject_id=_audit_subject_id(command.scope),
            event_type="operator_override_rejected",
            created_at=_audit_timestamp(event.occurred_at),
            actor=command.actor,
            payload={
                "schemaVersion": "operator-override-audit/v1",
                "outcome": event.outcome,
                "command": _audit_command_projection(command),
            },
        )
    )


def _override_to_row(
    override: ActiveOverride,
    audit_record: AuditEventRecord,
) -> dict[str, object]:
    command = override.command
    projection = {
        "schemaVersion": "operator-override-state/v1",
        "overrideId": override.override_id,
        "operationId": command.operation_id,
        "kind": command.kind,
        "scope": {
            "installationId": command.scope.installation_id,
            "repositoryId": command.scope.repository_id,
        },
        "subjectId": command.subject_id,
        "actor": command.actor,
        "reason": command.reason,
        "expiresAt": (None if command.expires_at is None else command.expires_at.isoformat()),
        "appliedAt": override.applied_at.isoformat(),
        "auditEventId": audit_record.audit_event_id,
        "auditInputHash": audit_record.input_hash,
    }
    canonical = encode_canonical_object(
        projection,
        maximum_bytes=_RECORD_MAXIMUM_BYTES,
        context="operator override record",
    )
    return {
        "override_id": override.override_id,
        "operation_id": command.operation_id,
        "installation_id": command.scope.installation_id,
        "repository_id": command.scope.repository_id,
        "kind": command.kind,
        "target_subject_id": command.subject_id if command.kind != "disable_omission" else None,
        "expires_at": command.expires_at,
        "applied_at": override.applied_at,
        "record_canonical_json": canonical,
        "semantic_hash": semantic_hash(projection),
        "audit_event_id": audit_record.audit_event_id,
        "audit_input_hash": bytes.fromhex(audit_record.input_hash),
    }


def decode_operator_override_row(row: Mapping[str, object]) -> StoredOverrideRecord:
    mapping = decode_canonical_object(
        row.get("record_canonical_json"),
        maximum_bytes=_RECORD_MAXIMUM_BYTES,
        context="operator override record",
    )
    require_exact_keys(
        mapping,
        {
            "schemaVersion",
            "overrideId",
            "operationId",
            "kind",
            "scope",
            "subjectId",
            "actor",
            "reason",
            "expiresAt",
            "appliedAt",
            "auditEventId",
            "auditInputHash",
        },
        "operator override record",
    )
    if mapping["schemaVersion"] != "operator-override-state/v1":
        raise CanonicalRowCodecError("stored operator override schema version is invalid")
    scope_mapping = mapping["scope"]
    if type(scope_mapping) is not dict:
        raise CanonicalRowCodecError("stored operator override scope is invalid")
    scope_mapping = cast(dict[str, object], scope_mapping)
    require_exact_keys(
        scope_mapping,
        {"installationId", "repositoryId"},
        "operator override scope",
    )
    scope = RepositoryScope(
        _exact_int(scope_mapping["installationId"], "installation id"),
        _exact_int(scope_mapping["repositoryId"], "repository id"),
    )
    expires_at = _optional_encoded_datetime(mapping["expiresAt"], "expiry")
    applied_at = _encoded_datetime(mapping["appliedAt"], "application time")
    kind = _override_kind(mapping["kind"])
    command = OverrideCommand(
        kind=kind,
        scope=scope,
        subject_id=_decode_subject(kind, mapping["subjectId"]),
        operation_id=require_bounded_text(
            mapping["operationId"], maximum_bytes=512, context="operator override operation"
        ),
        actor=require_bounded_text(
            mapping["actor"], maximum_bytes=512, context="operator override actor"
        ),
        reason=require_bounded_text(
            mapping["reason"], maximum_bytes=512, context="operator override reason"
        ),
        expires_at=expires_at,
    )
    rebuilt = ActiveOverride.create(command, applied_at)
    override_id = require_bounded_text(
        mapping["overrideId"], maximum_bytes=41, context="operator override id"
    )
    audit_event_id = require_bounded_text(
        mapping["auditEventId"], maximum_bytes=38, context="operator override audit event id"
    )
    audit_input_hash = require_digest(
        mapping["auditInputHash"], "operator override audit input hash"
    )
    if _OVERRIDE_ID.fullmatch(override_id) is None or rebuilt.override_id != override_id:
        raise CanonicalRowCodecError("stored operator override identity is invalid")
    if _AUDIT_EVENT_ID.fullmatch(audit_event_id) is None:
        raise CanonicalRowCodecError("stored operator override audit identity is invalid")
    target = command.subject_id if command.kind != "disable_omission" else None
    if (
        row.get("override_id") != override_id
        or row.get("operation_id") != command.operation_id
        or row.get("installation_id") != scope.installation_id
        or row.get("repository_id") != scope.repository_id
        or row.get("kind") != command.kind
        or row.get("target_subject_id") != target
        or _optional_required_datetime(row.get("expires_at"), "stored expiry") != expires_at
        or _required_datetime(row.get("applied_at"), "stored application time") != applied_at
        or row.get("semantic_hash") != semantic_hash(mapping)
        or row.get("audit_event_id") != audit_event_id
        or require_bytes(row.get("audit_input_hash"), "stored audit input hash")
        != bytes.fromhex(audit_input_hash)
    ):
        raise CanonicalRowCodecError("stored operator override columns disagree with its record")
    return StoredOverrideRecord(rebuilt, audit_event_id, audit_input_hash)


def _override_kind(
    value: object,
) -> Literal["force_full_ci", "disable_omission", "enable_omission"]:
    if value == "force_full_ci":
        return "force_full_ci"
    if value == "disable_omission":
        return "disable_omission"
    if value == "enable_omission":
        return "enable_omission"
    raise CanonicalRowCodecError("stored operator override kind is invalid")


def _decode_subject(kind: OverrideKind, value: object) -> str | None:
    if kind == "disable_omission":
        if value is not None:
            raise CanonicalRowCodecError("stored repository override subject must be null")
        return None
    if kind == "enable_omission":
        target = require_bounded_text(
            value,
            maximum_bytes=41,
            context="released operator override",
        )
        if _OVERRIDE_ID.fullmatch(target) is None:
            raise CanonicalRowCodecError("stored omission release target is invalid")
        return target
    return require_bounded_text(
        value,
        maximum_bytes=512,
        context="operator override subject",
    )


def _scope_operation_key(prefix: str, command: OverrideCommand) -> str:
    scope = command.scope
    return f"{prefix}:{scope.installation_id}:{scope.repository_id}:{command.operation_id}"


def _audit_subject_id(scope: RepositoryScope) -> str:
    return f"operator-override:{scope.installation_id}:{scope.repository_id}"


def _audit_command_projection(command: OverrideCommand) -> dict[str, JsonValue]:
    return {
        "kind": command.kind,
        "scope": {
            "installationId": command.scope.installation_id,
            "repositoryId": command.scope.repository_id,
        },
        "subjectId": command.subject_id,
        "operationId": command.operation_id,
        "actor": command.actor,
        "reason": command.reason,
        "expiresAt": (None if command.expires_at is None else _audit_timestamp(command.expires_at)),
    }


def _encoded_datetime(value: object, context: str) -> datetime:
    if type(value) is not str or not value:
        raise CanonicalRowCodecError(f"stored operator override {context} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CanonicalRowCodecError(f"stored operator override {context} is invalid") from error
    if parsed.tzinfo is None:
        raise CanonicalRowCodecError(f"stored operator override {context} lacks a timezone")
    return parsed


def _optional_encoded_datetime(value: object, context: str) -> datetime | None:
    return None if value is None else _encoded_datetime(value, context)


def _required_datetime(value: object, context: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise CanonicalRowCodecError(f"{context} is invalid")
    return value


def _optional_required_datetime(value: object, context: str) -> datetime | None:
    return None if value is None else _required_datetime(value, context)


def _exact_int(value: object, context: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
        raise CanonicalRowCodecError(f"stored operator override {context} is invalid")
    return value


def _audit_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
