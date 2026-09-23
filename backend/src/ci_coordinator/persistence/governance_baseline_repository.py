"""Atomic PostgreSQL append of owner-approved governance baselines."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventRecord
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE,
    GovernanceBaselineCommand,
    GovernanceBaselineConflict,
    GovernanceBaselineCreated,
    GovernanceBaselineDraft,
    GovernanceBaselineDuplicate,
    GovernanceBaselineOperationConflict,
    GovernanceBaselinePointer,
    GovernanceBaselineRecord,
    GovernanceBaselineResolution,
    GovernanceBaselineUnchanged,
    GovernanceBaselineWriteResult,
    PreparedGovernanceBaseline,
    canonical_instant,
    decode_governance_baseline_command,
    encode_governance_baseline_command,
    normalize_baseline_instant,
    prepare_governance_baseline,
)
from ci_coordinator.governance_observation import decode_governance_state
from ci_coordinator.persistence.audit_codec import row_to_record
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.scalar_rows import optional_int as _optional_int
from ci_coordinator.persistence.scalar_rows import optional_string as _optional_string
from ci_coordinator.persistence.scalar_rows import required_bytes as _required_bytes
from ci_coordinator.persistence.scalar_rows import required_int as _required_int
from ci_coordinator.persistence.scalar_rows import required_string as _required_string
from ci_coordinator.persistence.schema import (
    audit_events,
    governance_baseline_operations,
    governance_baselines,
)


@dataclass(frozen=True, slots=True)
class _StoredOperation:
    command: GovernanceBaselineCommand
    result_kind: Literal["accepted", "unchanged"]
    record: GovernanceBaselineRecord
    recorded_at: datetime


class _PostgresGovernanceBaselineRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit_events: _PostgresAuditEventRepository,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit_events = audit_events
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def resolve_operation(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineResolution:
        self._ensure_active()
        if type(command) is not GovernanceBaselineCommand:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("governance baseline command must be exact")
        try:
            await lock_repository_scope(self._connection, command.scope)
            existing = await self._load_operation(command.scope, command.operation_id)
            return _resolve_existing(existing, command)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("governance baseline operation lookup failed") from error

    async def load_active(
        self,
        scope: RepositoryScope,
    ) -> GovernanceBaselineRecord | None:
        self._ensure_active()
        if type(scope) is not RepositoryScope:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("governance baseline scope must be exact")
        try:
            result = await self._connection.execute(
                select(governance_baselines)
                .where(
                    governance_baselines.c.installation_id == scope.installation_id,
                    governance_baselines.c.repository_id == scope.repository_id,
                )
                .order_by(governance_baselines.c.version.desc())
                .limit(1)
            )
            row = result.mappings().one_or_none()
            return None if row is None else await self._record_from_row(dict(row))
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("governance baseline active read failed") from error

    async def accept(
        self,
        prepared: PreparedGovernanceBaseline,
    ) -> GovernanceBaselineWriteResult:
        self._ensure_active()
        if type(prepared) is not PreparedGovernanceBaseline:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("governance baseline must be exactly prepared")
        draft = prepared.draft
        command = draft.command
        try:
            await lock_repository_scope(self._connection, command.scope)
            existing = await self._load_operation(command.scope, command.operation_id)
            resolution = _resolve_existing(existing, command)
            if resolution is not None:
                return resolution

            active = await self._load_active_unchecked(command.scope)
            active_pointer = None if active is None else active.pointer
            if active_pointer != command.expected_active:
                return GovernanceBaselineConflict(active_pointer)
            if active is not None and active.state_bytes == draft.state_bytes:
                await self._connection.execute(
                    insert(governance_baseline_operations).values(
                        _operation_to_row(
                            command,
                            result_kind="unchanged",
                            record=active,
                            recorded_at=prepared.approved_at,
                        )
                    )
                )
                return GovernanceBaselineUnchanged(active)

            appended = await self._audit_events._append_pair_owned(
                prepared._take_audit_event(),
                command.scope,
            )
            if not isinstance(appended, AuditAppendAppended):
                raise PersistenceInvariantViolation(
                    "governance baseline audit idempotency contradicts its operation"
                )
            record = GovernanceBaselineRecord.from_draft(
                draft,
                approved_at=prepared.approved_at,
                audit_event_id=appended.record.audit_event_id,
                audit_input_hash=appended.record.input_hash,
            )
            await self._connection.execute(
                insert(governance_baselines).values(_record_to_row(record))
            )
            await self._connection.execute(
                insert(governance_baseline_operations).values(
                    _operation_to_row(
                        command,
                        result_kind="accepted",
                        record=record,
                        recorded_at=prepared.approved_at,
                    )
                )
            )
            return GovernanceBaselineCreated(record)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("governance baseline append failed") from error

    async def _load_active_unchecked(
        self,
        scope: RepositoryScope,
    ) -> GovernanceBaselineRecord | None:
        result = await self._connection.execute(
            select(governance_baselines)
            .where(
                governance_baselines.c.installation_id == scope.installation_id,
                governance_baselines.c.repository_id == scope.repository_id,
            )
            .order_by(governance_baselines.c.version.desc())
            .limit(1)
        )
        row = result.mappings().one_or_none()
        return None if row is None else await self._record_from_row(dict(row))

    async def _load_operation(
        self,
        scope: RepositoryScope,
        operation_id: str,
    ) -> _StoredOperation | None:
        result = await self._connection.execute(
            select(governance_baseline_operations).where(
                governance_baseline_operations.c.installation_id == scope.installation_id,
                governance_baseline_operations.c.repository_id == scope.repository_id,
                governance_baseline_operations.c.operation_id == operation_id,
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        try:
            operation_row = dict(row)
            command = decode_governance_baseline_command(
                _required_bytes(operation_row, "command_canonical_json")
            )
            if command.scope != scope or command.operation_id != operation_id:
                raise PersistenceInvariantViolation(
                    "governance baseline operation identity is inconsistent"
                )
            baseline_result = await self._connection.execute(
                select(governance_baselines).where(
                    governance_baselines.c.installation_id == scope.installation_id,
                    governance_baselines.c.repository_id == scope.repository_id,
                    governance_baselines.c.version
                    == _required_int(operation_row, "result_version"),
                    governance_baselines.c.baseline_id
                    == _required_string(operation_row, "result_baseline_id"),
                )
            )
            baseline_row = baseline_result.mappings().one_or_none()
            if baseline_row is None:
                raise PersistenceInvariantViolation(
                    "governance baseline operation result is missing"
                )
            record = await self._record_from_row(dict(baseline_row))
            raw_result_kind = _required_string(operation_row, "result_kind")
            recorded_at = _required_datetime(operation_row, "recorded_at")
            if recorded_at != normalize_baseline_instant(recorded_at):
                raise PersistenceInvariantViolation(
                    "governance baseline operation time is not canonical"
                )
            if raw_result_kind == "accepted":
                result_kind: Literal["accepted", "unchanged"] = "accepted"
                if command != record.command or recorded_at != record.approved_at:
                    raise PersistenceInvariantViolation(
                        "accepted governance baseline operation is inconsistent"
                    )
            elif raw_result_kind == "unchanged":
                result_kind = "unchanged"
                if (
                    command.expected_active != record.pointer
                    or command.expected_state_digest != record.pointer.state_digest
                    or recorded_at < record.observed_at
                ):
                    raise PersistenceInvariantViolation(
                        "unchanged governance baseline operation is inconsistent"
                    )
            else:
                raise PersistenceInvariantViolation(
                    "governance baseline operation result kind is invalid"
                )
            return _StoredOperation(
                command=command,
                result_kind=result_kind,
                record=record,
                recorded_at=recorded_at,
            )
        except PersistenceInvariantViolation:
            raise
        except (KeyError, TypeError, UnicodeError, ValueError) as error:
            raise PersistenceInvariantViolation(
                "stored governance baseline operation is invalid"
            ) from error

    async def _record_from_row(
        self,
        row: Mapping[str, object],
    ) -> GovernanceBaselineRecord:
        try:
            scope = RepositoryScope(
                _required_int(row, "installation_id"),
                _required_int(row, "repository_id"),
            )
            state = decode_governance_state(_required_bytes(row, "state_canonical_json"))
            if state.repository.scope != scope:
                raise PersistenceInvariantViolation(
                    "governance baseline state crosses repository scope"
                )
            predecessor = _predecessor_from_row(scope, row)
            event = await self._load_audit_event(
                scope,
                _required_string(row, "audit_event_id"),
                _required_string(row, "baseline_id"),
            )
            record = GovernanceBaselineRecord(
                command=GovernanceBaselineCommand(
                    scope=scope,
                    operation_id=_required_string(row, "operation_id"),
                    expected_state_digest=_required_string(row, "state_digest"),
                    expected_active=predecessor,
                    actor=_required_string(row, "actor"),
                    reason=_required_string(row, "reason"),
                ),
                baseline_id=_required_string(row, "baseline_id"),
                version=_required_int(row, "version"),
                state=state,
                observed_at=_required_datetime(row, "observed_at"),
                approved_at=_required_datetime(row, "approved_at"),
                audit_event_id=event.audit_event_id,
                audit_input_hash=_required_bytes(row, "audit_input_hash").hex(),
            )
            await self._verify_predecessor(record)
            retained = prepare_governance_baseline(
                GovernanceBaselineDraft(
                    command=record.command,
                    state=record.state,
                    observed_at=record.observed_at,
                ),
                approved_at=record.approved_at,
            )
            if (
                event.actor != record.command.actor
                or event.created_at != canonical_instant(record.approved_at)
                or event.input_hash != record.audit_input_hash
                or retained.audit_input_hash != record.audit_input_hash
            ):
                raise PersistenceInvariantViolation(
                    "governance baseline row and audit event are inconsistent"
                )
            return record
        except PersistenceInvariantViolation:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise PersistenceInvariantViolation("stored governance baseline is invalid") from error

    async def _verify_predecessor(self, record: GovernanceBaselineRecord) -> None:
        predecessor = record.command.expected_active
        if predecessor is None:
            return
        result = await self._connection.execute(
            select(governance_baselines.c.state_digest).where(
                governance_baselines.c.installation_id == predecessor.scope.installation_id,
                governance_baselines.c.repository_id == predecessor.scope.repository_id,
                governance_baselines.c.version == predecessor.version,
                governance_baselines.c.baseline_id == predecessor.baseline_id,
            )
        )
        if result.scalar_one_or_none() != predecessor.state_digest:
            raise PersistenceInvariantViolation(
                "governance baseline predecessor evidence is inconsistent"
            )

    async def _load_audit_event(
        self,
        scope: RepositoryScope,
        audit_event_id: str,
        baseline_id: str,
    ) -> AuditEventRecord:
        result = await self._connection.execute(
            select(audit_events).where(audit_events.c.audit_event_id == audit_event_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise PersistenceInvariantViolation("governance baseline audit event is missing")
        event = row_to_record(dict(row))
        if (
            event.installation_id != scope.installation_id
            or event.repository_id != scope.repository_id
            or event.subject_id != baseline_id
            or event.event_type != GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE
        ):
            raise PersistenceInvariantViolation("governance baseline audit event is inconsistent")
        return event


def _resolve_existing(
    existing: _StoredOperation | None,
    command: GovernanceBaselineCommand,
) -> GovernanceBaselineResolution:
    if existing is None:
        return None
    if existing.command != command:
        return GovernanceBaselineOperationConflict(existing.record)
    if existing.result_kind == "unchanged":
        return GovernanceBaselineUnchanged(existing.record)
    return GovernanceBaselineDuplicate(existing.record)


def _record_to_row(record: GovernanceBaselineRecord) -> dict[str, object]:
    predecessor = record.command.expected_active
    return {
        "installation_id": record.command.scope.installation_id,
        "repository_id": record.command.scope.repository_id,
        "version": record.version,
        "baseline_id": record.baseline_id,
        "operation_id": record.command.operation_id,
        "state_digest": record.command.expected_state_digest,
        "state_canonical_json": record.state_bytes,
        "observed_at": record.observed_at,
        "approved_at": record.approved_at,
        "actor": record.command.actor,
        "reason": record.command.reason,
        "supersedes_baseline_id": None if predecessor is None else predecessor.baseline_id,
        "supersedes_version": None if predecessor is None else predecessor.version,
        "supersedes_state_digest": None if predecessor is None else predecessor.state_digest,
        "audit_event_id": record.audit_event_id,
        "audit_input_hash": bytes.fromhex(record.audit_input_hash),
    }


def _operation_to_row(
    command: GovernanceBaselineCommand,
    *,
    result_kind: Literal["accepted", "unchanged"],
    record: GovernanceBaselineRecord,
    recorded_at: datetime,
) -> dict[str, object]:
    return {
        "installation_id": command.scope.installation_id,
        "repository_id": command.scope.repository_id,
        "operation_id": command.operation_id,
        "command_canonical_json": encode_governance_baseline_command(command),
        "result_kind": result_kind,
        "result_baseline_id": record.baseline_id,
        "result_version": record.version,
        "recorded_at": recorded_at,
    }


def _predecessor_from_row(
    scope: RepositoryScope,
    row: Mapping[str, object],
) -> GovernanceBaselinePointer | None:
    baseline_id = _optional_string(row, "supersedes_baseline_id")
    version = _optional_int(row, "supersedes_version")
    state_digest = _optional_string(row, "supersedes_state_digest")
    if baseline_id is None and version is None and state_digest is None:
        return None
    if baseline_id is None or version is None or state_digest is None:
        raise PersistenceInvariantViolation("governance baseline predecessor shape is invalid")
    return GovernanceBaselinePointer(scope, baseline_id, version, state_digest)


def _required_datetime(row: Mapping[str, object], key: str) -> datetime:
    value = row[key]
    if type(value) is not datetime:
        raise TypeError(f"{key} is not a datetime")
    return value
