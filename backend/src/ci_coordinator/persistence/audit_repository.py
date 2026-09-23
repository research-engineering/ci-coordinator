from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.app.planning_evidence import PLANNING_EVIDENCE_AUDIT_EVENT_TYPE
from ci_coordinator.app.reconciliation_audit import RECONCILIATION_TERMINAL_AUDIT_EVENT_TYPE
from ci_coordinator.audit_replay import (
    AUDIT_REPLAY_PAGE_SIZE,
    AuditAppendAppended,
    AuditAppendConflict,
    AuditAppendDuplicate,
    AuditAppendResult,
    AuditEventError,
    AuditEventRecord,
    AuditLedgerSnapshot,
    PreparedAuditEvent,
    build_prepared_audit_event,
)
from ci_coordinator.ci_economics.analytics_configuration import PURPOSE_CONFIGURED_EVENT
from ci_coordinator.ci_economics.budget_commands import BUDGET_POLICY_EVENT_TYPE
from ci_coordinator.ci_economics.history_commands import HISTORY_CONFIGURED_EVENT_TYPE
from ci_coordinator.ci_economics.history_gap_recovery import HISTORY_GAPS_REQUEUED_EVENT_TYPE
from ci_coordinator.ci_economics.history_retention_commands import HISTORY_RETENTION_EVENT
from ci_coordinator.ci_economics.observation_commands import OBSERVATION_CONFIGURED_EVENT_TYPE
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs.contracts import is_pair_owned_audit_event_type
from ci_coordinator.config_epochs.registration import (
    is_pair_owned_config_registration_event_type,
)
from ci_coordinator.governance_baseline import (
    is_pair_owned_governance_baseline_event_type,
)
from ci_coordinator.operator_controls.override import (
    OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
)
from ci_coordinator.persistence.audit_codec import (
    idempotency_key_digest,
    prepared_record_to_row,
    row_to_record,
)
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.issued_plan_audit import ISSUED_PLAN_AUDIT_EVENT_TYPE
from ci_coordinator.persistence.schema import audit_events, audit_ledger_head
from ci_coordinator.production_admission.cutover_commands import PRODUCTION_CUTOVER_EVENT_TYPE
from ci_coordinator.proposal_review import is_pair_owned_proposal_review_event_type


class _PostgresAuditEventRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def append(self, event: PreparedAuditEvent) -> AuditAppendResult:
        self._ensure_active()
        if type(event) is not PreparedAuditEvent:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("audit append requires an exact prepared event")
        if _is_pair_owned_audit_event_type(event.event_type):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation(
                "pair-owned audit events require their owning state transition"
            )
        try:
            return await self._append(event, scope=_prepared_scope(event))
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("audit append failed") from error

    async def _append_pair_owned(
        self,
        event: PreparedAuditEvent,
        scope: RepositoryScope,
    ) -> AuditAppendResult:
        """Append an exact lifecycle-owned event inside its state transaction."""
        self._ensure_active()
        if (
            type(event) is not PreparedAuditEvent
            or type(scope) is not RepositoryScope
            or not _is_pair_owned_audit_event_type(event.event_type)
        ):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("pair-owned audit event is invalid")
        try:
            return await self._append(event, scope=scope)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("pair-owned audit append failed") from error

    async def _find_pair_owned(
        self,
        idempotency_key: str,
        event_type: str,
        scope: RepositoryScope,
    ) -> AuditEventRecord | None:
        self._ensure_active()
        if type(scope) is not RepositoryScope or not _is_pair_owned_audit_event_type(event_type):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("pair-owned audit lookup type is invalid")
        try:
            existing_row = await self._find_row_by_idempotency_key(idempotency_key)
            if existing_row is None:
                return None
            _require_stored_scope(existing_row, scope)
            existing = row_to_record(existing_row)
            if existing is not None and existing.event_type != event_type:
                raise PersistenceInvariantViolation(
                    "pair-owned audit key resolves to another event type"
                )
            return existing
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("pair-owned audit lookup failed") from error

    async def _append(
        self,
        prepared: PreparedAuditEvent,
        *,
        scope: RepositoryScope | None,
    ) -> AuditAppendResult:
        _require_prepared_scope(prepared, scope)
        head = await self._lock_head()
        previous = await self._previous_from_head(head)
        try:
            attempted = build_prepared_audit_event(prepared, previous)
        except AuditEventError as error:
            raise PersistenceInvariantViolation("audit ledger cannot be extended") from error
        existing_row = await self._find_row_by_idempotency_key(prepared.idempotency_key)
        if existing_row is not None:
            _require_stored_scope(existing_row, scope)
            existing = row_to_record(existing_row)
            if existing.idempotency_key != prepared.idempotency_key:
                raise PersistenceInvariantViolation("audit idempotency digest collision")
            if existing.input_hash == attempted.input_hash:
                return AuditAppendDuplicate(record=existing)
            return AuditAppendConflict(
                existing=existing,
                attempted=attempted,
                reason=(
                    f"audit event idempotency key {attempted.idempotency_key} "
                    "already exists with different content"
                ),
            )
        await self._reject_event_id_collision(attempted.audit_event_id)
        record = attempted
        row = prepared_record_to_row(record, prepared)
        await self._connection.execute(insert(audit_events).values(row))
        revision = _required_int(head, "revision")
        updated = await self._connection.execute(
            update(audit_ledger_head)
            .where(
                audit_ledger_head.c.head_id == 1,
                audit_ledger_head.c.revision == revision,
            )
            .values(
                revision=record.sequence,
                last_sequence=record.sequence,
                last_event_hash=bytes.fromhex(record.event_hash),
            )
            .returning(audit_ledger_head.c.revision)
        )
        if updated.scalar_one_or_none() != record.sequence:
            raise PersistenceInvariantViolation("audit ledger head compare-and-set failed")
        return AuditAppendAppended(record=record)

    async def snapshot(self) -> AuditLedgerSnapshot:
        self._ensure_active()
        try:
            return await load_audit_ledger_snapshot(self._connection)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("audit replay snapshot failed") from error

    async def load_page(
        self,
        *,
        after_sequence: int,
        through_sequence: int,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        self._ensure_active()
        if type(limit) is not int or not 1 <= limit <= AUDIT_REPLAY_PAGE_SIZE:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("audit replay page limit is invalid")
        try:
            return await load_audit_records_after(
                self._connection,
                after_sequence,
                through_sequence=through_sequence,
                limit=limit,
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceInvariantViolation:
            self._mark_rollback_required()
            raise
        except ValueError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("audit replay page bounds are invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("audit replay page read failed") from error

    async def _lock_head(self) -> dict[str, object]:
        result = await self._connection.execute(
            select(audit_ledger_head).where(audit_ledger_head.c.head_id == 1).with_for_update()
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise PersistenceInvariantViolation("audit ledger head is missing")
        return dict(row)

    async def _previous_from_head(self, head: dict[str, object]) -> AuditEventRecord | None:
        revision = _required_int(head, "revision")
        if revision == 0:
            if head["last_sequence"] is not None or head["last_event_hash"] is not None:
                raise PersistenceInvariantViolation("empty audit ledger head is inconsistent")
            return None
        result = await self._connection.execute(
            select(audit_events).where(audit_events.c.sequence == revision)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise PersistenceInvariantViolation("audit ledger predecessor is missing")
        previous = row_to_record(dict(row))
        last_hash = head["last_event_hash"]
        if isinstance(last_hash, memoryview):
            last_hash = last_hash.tobytes()
        if head["last_sequence"] != previous.sequence or last_hash != bytes.fromhex(
            previous.event_hash
        ):
            raise PersistenceInvariantViolation("audit ledger head is inconsistent")
        return previous

    async def _find_by_idempotency_key(self, key: str) -> AuditEventRecord | None:
        row = await self._find_row_by_idempotency_key(key)
        return None if row is None else row_to_record(row)

    async def _find_row_by_idempotency_key(self, key: str) -> dict[str, object] | None:
        result = await self._connection.execute(
            select(audit_events).where(
                audit_events.c.idempotency_key_digest == idempotency_key_digest(key)
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

    async def _reject_event_id_collision(self, audit_event_id: str) -> None:
        result = await self._connection.execute(
            select(audit_events.c.sequence).where(audit_events.c.audit_event_id == audit_event_id)
        )
        if result.scalar_one_or_none() is not None:
            raise PersistenceInvariantViolation(f"audit event {audit_event_id} already exists")


def _required_int(row: dict[str, object], key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise PersistenceInvariantViolation(f"audit ledger head {key} is invalid")
    return value


def _require_prepared_scope(
    event: PreparedAuditEvent,
    expected: RepositoryScope | None,
) -> None:
    if expected is None:
        if event.installation_id is not None or event.repository_id is not None:
            raise PersistenceInvariantViolation(
                "generic audit append cannot retain a repository-owned event"
            )
        return
    if type(expected) is not RepositoryScope or (
        event.installation_id != expected.installation_id
        or event.repository_id != expected.repository_id
    ):
        raise PersistenceInvariantViolation(
            "pair-owned audit event does not bind its repository scope"
        )


def _prepared_scope(event: PreparedAuditEvent) -> RepositoryScope | None:
    installation_id = event.installation_id
    repository_id = event.repository_id
    if installation_id is None and repository_id is None:
        return None
    if type(installation_id) is not int or type(repository_id) is not int:
        raise PersistenceInvariantViolation("audit event repository scope is incomplete")
    return RepositoryScope(installation_id, repository_id)


def _require_stored_scope(
    row: dict[str, object],
    expected: RepositoryScope | None,
) -> None:
    installation_id = row.get("installation_id")
    repository_id = row.get("repository_id")
    if expected is None:
        if installation_id is not None or repository_id is not None:
            raise PersistenceInvariantViolation(
                "audit idempotency key resolves to another repository scope"
            )
        return
    if type(expected) is not RepositoryScope or (
        installation_id != expected.installation_id or repository_id != expected.repository_id
    ):
        raise PersistenceInvariantViolation(
            "audit idempotency key resolves to another repository scope"
        )


def _is_pair_owned_audit_event_type(value: object) -> bool:
    return type(value) is str and (
        value
        in {
            ISSUED_PLAN_AUDIT_EVENT_TYPE,
            BUDGET_POLICY_EVENT_TYPE,
            OBSERVATION_CONFIGURED_EVENT_TYPE,
            HISTORY_CONFIGURED_EVENT_TYPE,
            HISTORY_GAPS_REQUEUED_EVENT_TYPE,
            PURPOSE_CONFIGURED_EVENT,
            HISTORY_RETENTION_EVENT,
            OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE,
            PLANNING_EVIDENCE_AUDIT_EVENT_TYPE,
            RECONCILIATION_TERMINAL_AUDIT_EVENT_TYPE,
            PRODUCTION_CUTOVER_EVENT_TYPE,
        }
        or is_pair_owned_audit_event_type(value)
        or is_pair_owned_config_registration_event_type(value)
        or is_pair_owned_proposal_review_event_type(value)
        or is_pair_owned_governance_baseline_event_type(value)
    )


async def load_audit_ledger_snapshot(
    connection: AsyncConnection,
) -> AuditLedgerSnapshot:
    maximum_sequence = select(func.max(audit_events.c.sequence)).scalar_subquery()
    result = await connection.execute(
        select(
            audit_ledger_head.c.revision,
            audit_ledger_head.c.last_sequence,
            audit_ledger_head.c.last_event_hash,
            maximum_sequence.label("maximum_sequence"),
        ).where(audit_ledger_head.c.head_id == 1)
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise PersistenceInvariantViolation("audit ledger head is missing")
    values = dict(row)
    revision = _required_int(values, "revision")
    last_sequence = values["last_sequence"]
    if last_sequence != (revision or None):
        raise PersistenceInvariantViolation("audit ledger head sequence is inconsistent")
    last_hash = values["last_event_hash"]
    if isinstance(last_hash, memoryview):
        last_hash = last_hash.tobytes()
    if last_hash is not None and (type(last_hash) is not bytes or len(last_hash) != 32):
        raise PersistenceInvariantViolation("audit ledger head hash is invalid")
    maximum = values["maximum_sequence"]
    if maximum is not None and type(maximum) is not int:
        raise PersistenceInvariantViolation("audit ledger maximum sequence is invalid")
    try:
        return AuditLedgerSnapshot(
            last_sequence=revision,
            last_event_hash=None if last_hash is None else last_hash.hex(),
            maximum_sequence=maximum,
        )
    except ValueError as error:
        raise PersistenceInvariantViolation("audit ledger snapshot is invalid") from error


async def load_audit_records_after(
    connection: AsyncConnection,
    sequence: int,
    *,
    through_sequence: int,
    limit: int,
) -> tuple[AuditEventRecord, ...]:
    if type(sequence) is not int or sequence < 0:
        raise ValueError("audit suffix sequence must be a non-negative integer")
    if type(through_sequence) is not int or through_sequence < sequence:
        raise ValueError("audit suffix upper bound must not precede its checkpoint")
    if type(limit) is not int or limit < 1:
        raise ValueError("audit suffix limit must be positive")
    statement = (
        select(audit_events)
        .where(
            audit_events.c.sequence > sequence,
            audit_events.c.sequence <= through_sequence,
        )
        .order_by(audit_events.c.sequence)
        .limit(limit)
    )
    result = await connection.execute(statement)
    return tuple(row_to_record(dict(row)) for row in result.mappings())
