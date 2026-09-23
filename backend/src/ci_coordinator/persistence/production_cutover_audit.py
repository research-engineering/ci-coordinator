from __future__ import annotations

from datetime import datetime
from typing import cast

from ci_coordinator.audit_replay import (
    AuditEventInput,
    AuditEventRecord,
    PreparedAuditEvent,
    prepare_audit_event,
)
from ci_coordinator.audit_replay._event_contracts import JsonValue
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.canonical_row import require_exact_keys
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.production_admission._fields import (
    _nonnegative_integer,
    _positive_integer,
    _text,
)
from ci_coordinator.production_admission.cutover_commands import (
    PRODUCTION_CUTOVER_EVENT_TYPE,
    ProductionCutoverApplied,
    ProductionCutoverCommand,
    ProductionCutoverRejected,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState

_CUTOVER_AUDIT_SCHEMA = "ci-coordinator.production-cutover-audit/v1"


def prepare_cutover_audit(
    command: ProductionCutoverCommand, state: ProductionScopeState, at: datetime
) -> PreparedAuditEvent:
    return prepare_audit_event(
        AuditEventInput(
            idempotency_key=command.audit_key,
            subject_type="release-evidence",
            subject_id=command.authority_id,
            event_type=PRODUCTION_CUTOVER_EVENT_TYPE,
            created_at=at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            actor=command.actor,
            installation_id=command.scope.installation_id,
            repository_id=command.scope.repository_id,
            payload=cast(
                JsonValue,
                {
                    "schemaVersion": _CUTOVER_AUDIT_SCHEMA,
                    "command": command.to_mapping(),
                    "commandDigest": command.command_digest,
                    "state": state.to_mapping(),
                },
            ),
        )
    )


def replay_cutover_audit(
    event: AuditEventRecord, command: ProductionCutoverCommand
) -> ProductionCutoverApplied | ProductionCutoverRejected:
    payload = event.payload
    if type(payload) is not dict:
        raise PersistenceInvariantViolation("production cutover audit payload is invalid")
    require_exact_keys(
        payload, {"schemaVersion", "command", "commandDigest", "state"}, "production cutover audit"
    )
    if payload["schemaVersion"] != _CUTOVER_AUDIT_SCHEMA:
        raise PersistenceInvariantViolation("production cutover audit schema is invalid")
    if (
        payload["command"] != command.to_mapping()
        or payload["commandDigest"] != command.command_digest
    ):
        return ProductionCutoverRejected("command_conflict")
    if (
        event.idempotency_key != command.audit_key
        or event.actor != command.actor
        or event.event_type != PRODUCTION_CUTOVER_EVENT_TYPE
        or event.subject_type != "release-evidence"
        or event.subject_id != command.authority_id
        or event.installation_id != command.scope.installation_id
        or event.repository_id != command.scope.repository_id
    ):
        raise PersistenceInvariantViolation("production cutover audit coordinates conflict")
    try:
        state = _state(payload["state"])
    except (TypeError, ValueError) as error:
        raise PersistenceInvariantViolation("production cutover audit state is invalid") from error
    if state.scope != command.scope or state.revision != command.expected_revision + 1:
        raise PersistenceInvariantViolation("production cutover audit transition conflicts")
    return ProductionCutoverApplied(state, duplicate=True)


def _state(value: JsonValue) -> ProductionScopeState:
    if type(value) is not dict:
        raise ValueError("production cutover state must be an object")
    require_exact_keys(
        value,
        {
            "installationId",
            "repositoryId",
            "revision",
            "generation",
            "revokedThroughGeneration",
            "activeAuthorityId",
            "activeSubjectDigest",
            "stagedAuthorityId",
            "latchOverrideId",
            "latchAppliedAt",
        },
        "production cutover state",
    )
    timestamp = value["latchAppliedAt"]
    state = ProductionScopeState(
        scope=RepositoryScope(
            _positive_integer(value["installationId"]), _positive_integer(value["repositoryId"])
        ),
        revision=_positive_integer(value["revision"]),
        generation=_nonnegative_integer(value["generation"]),
        revoked_through_generation=_nonnegative_integer(value["revokedThroughGeneration"]),
        active_authority_id=_optional_text(value["activeAuthorityId"]),
        active_subject_digest=_optional_text(value["activeSubjectDigest"]),
        staged_authority_id=_optional_text(value["stagedAuthorityId"]),
        latch_override_id=_optional_text(value["latchOverrideId"]),
        latch_applied_at=None if timestamp is None else datetime.fromisoformat(_text(timestamp)),
    )
    if state.to_mapping() != value:
        raise ValueError("production cutover state does not round-trip")
    return state


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)
