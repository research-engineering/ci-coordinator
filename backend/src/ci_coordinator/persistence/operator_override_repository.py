"""Transactional persistence adapters for durable operator overrides."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendConflict,
    AuditAppendDuplicate,
    AuditEventRecord,
    PreparedAuditEvent,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.override import (
    ActiveOverride,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.operator_controls.resolution import (
    ActiveOverrideRecords,
    OverrideLookupResult,
    OverrideLookupUnavailable,
)
from ci_coordinator.operator_controls.use_cases import (
    OverrideApplied,
    OverrideConflict,
    OverrideDuplicate,
    OverrideReplayResult,
    OverrideStoreResult,
    OverrideUnavailable,
)
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.canonical_row import (
    CanonicalRowCodecError,
    require_bounded_text,
)
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    required_capabilities,
)
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.compatibility_repository import admit_current_capabilities
from ci_coordinator.persistence.errors import (
    DatabaseCapabilityUnavailable,
    PersistenceError,
    PersistenceInvariantViolation,
    StoreUnavailable,
)
from ci_coordinator.persistence.operator_override_attestation import (
    operator_override_runtime_principal_is_restricted,
    operator_override_state_schema_matches_contract,
    operator_override_state_schema_matches_contract_sync,
)
from ci_coordinator.persistence.operator_override_codec import (
    StoredOverrideRecord,
    _override_to_row,
    _prepare_applied_event,
    _prepare_rejection_event,
    decode_operator_override_row,
)
from ci_coordinator.persistence.operator_override_schema import operator_overrides
from ci_coordinator.persistence.production_cutover_schema_attestation import (
    production_cutover_schema_matches_contract,
)
from ci_coordinator.persistence.repository_scope_lock import lock_repository_scope
from ci_coordinator.persistence.schema import audit_events, production_scope_states
from ci_coordinator.persistence.schema_capabilities import (
    AUDIT_LEDGER,
    DATABASE_COMPATIBILITY_PROTOCOL,
    OPERATOR_OVERRIDE_STATE,
    PRODUCTION_GENERATION_CUTOVER,
)
from ci_coordinator.persistence.unit_of_work import PostgresUnitOfWork

__all__ = (
    "OPERATOR_OVERRIDE_STATE",
    "DurableOperatorOverrideStore",
    "OperatorOverrideUnitOfWorkFactory",
    "PostgresOperatorOverrideRepository",
    "PostgresOperatorOverrideUnitOfWork",
    "admit_operator_override_state",
    "operator_override_runtime_principal_is_restricted",
    "operator_override_state_requirements",
    "operator_override_state_schema_matches_contract",
    "operator_override_state_schema_matches_contract_sync",
)

type OperatorOverrideUnitOfWorkFactory = Callable[[], "PostgresOperatorOverrideUnitOfWork"]
type _StateWrite = Literal["inserted", "duplicate", "conflict"]


@dataclass(frozen=True, slots=True)
class _SequencedOverride:
    record: StoredOverrideRecord
    audit_sequence: int

    @property
    def override(self) -> ActiveOverride:
        return self.record.override


def _prepared_audit_identity(event: PreparedAuditEvent) -> tuple[object, ...]:
    return (
        event.idempotency_key,
        event.subject_type,
        event.subject_id,
        event.event_type,
        event.created_at,
        event.actor,
        event.payload_canonical_bytes,
        event.payload_hash,
        event.input_hash,
    )


class PostgresOperatorOverrideRepository:
    """Transaction-scoped state repository paired with the transaction audit ledger."""

    def __init__(
        self,
        connection: AsyncConnection,
        audit_events: _PostgresAuditEventRepository,
        profile: CompatibilityProfile,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit_events = audit_events
        self._profile = profile
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def apply(
        self,
        override: ActiveOverride,
        prepared_audit: PreparedAuditEvent,
    ) -> OverrideStoreResult:
        self._ensure_active()
        if type(override) is not ActiveOverride or type(prepared_audit) is not PreparedAuditEvent:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("operator override pair must be exact")
        expected_audit = _prepare_applied_event(override, OverrideAuditEvent.applied(override))
        if _prepared_audit_identity(prepared_audit) != _prepared_audit_identity(expected_audit):
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("operator override audit does not match its state")
        try:
            await self._admit_state()
            await lock_repository_scope(self._connection, override.command.scope)
            await self._lock_operation(override.command)
            existing = await self._load_operation(override.command)
            if existing is not None:
                if existing.override.command == override.command:
                    return OverrideDuplicate(existing.override)
                return OverrideConflict()
            if not await self._transition_is_admitted(override):
                return OverrideConflict()
            audit_result = await self._audit_events._append_pair_owned(
                prepared_audit,
                override.command.scope,
            )
            if isinstance(audit_result, AuditAppendConflict):
                return OverrideConflict()
            if not isinstance(audit_result, AuditAppendAppended | AuditAppendDuplicate):
                raise PersistenceInvariantViolation("operator override audit outcome is invalid")
            state_result = await self._write_state(override, audit_result.record)
            if state_result == "conflict":
                self._mark_rollback_required()
                return OverrideConflict()
            if isinstance(audit_result, AuditAppendAppended) and state_result == "inserted":
                return OverrideApplied(override)
            return OverrideDuplicate(override)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceError:
            self._mark_rollback_required()
            raise
        except (CanonicalRowCodecError, SQLAlchemyError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("operator override persistence failed") from error

    async def resolve_operation(self, command: OverrideCommand) -> OverrideReplayResult:
        self._ensure_active()
        if type(command) is not OverrideCommand:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("operator override command must be exact")
        try:
            await self._admit_state()
            await self._lock_operation(command)
            existing = await self._load_operation(command)
            if existing is None:
                return None
            if existing.override.command == command:
                return OverrideDuplicate(existing.override)
            return OverrideConflict()
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceError:
            self._mark_rollback_required()
            raise
        except (CanonicalRowCodecError, SQLAlchemyError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("operator override operation lookup failed") from error

    async def resolve_active(
        self,
        *,
        scope: RepositoryScope,
        subject_id: str | None,
        now: datetime,
    ) -> OverrideLookupResult:
        self._ensure_active()
        _require_resolution_query(scope, subject_id, now)
        try:
            await self._admit_state()
            force = await self._load_active_force(scope, subject_id, now)
            if isinstance(force, OverrideLookupUnavailable):
                return force
            control = await self._load_latest_omission_control(scope, now)
            disable = None
            if control is not None:
                if control.override.command.kind == "disable_omission":
                    disable = control.override
                elif control.override.command.kind == "enable_omission":
                    await self._assert_valid_enable_transition(scope, control)
                else:
                    raise PersistenceInvariantViolation("stored omission control kind is invalid")
            return ActiveOverrideRecords(force_full_ci=force, disable_dynamic=disable)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except PersistenceError:
            self._mark_rollback_required()
            raise
        except (CanonicalRowCodecError, SQLAlchemyError, ValueError) as error:
            self._mark_rollback_required()
            raise StoreUnavailable("operator override resolution failed") from error

    async def _lock_operation(self, command: OverrideCommand) -> None:
        await self._connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:operation_key AS text), 0))"),
            {
                "operation_key": (
                    f"operator-override:{command.scope.installation_id}:"
                    f"{command.scope.repository_id}:{command.operation_id}"
                )
            },
        )

    async def _transition_is_admitted(self, override: ActiveOverride) -> bool:
        kind = override.command.kind
        if kind == "force_full_ci":
            return True
        latest = await self._load_latest_omission_control(
            override.command.scope,
            None,
        )
        if latest is not None and override.applied_at < latest.override.applied_at:
            return False
        if kind == "disable_omission":
            return latest is None or latest.override.command.kind == "enable_omission"
        cutover_latch = await self._connection.scalar(
            select(production_scope_states.c.latch_override_id).where(
                production_scope_states.c.installation_id == override.command.scope.installation_id,
                production_scope_states.c.repository_id == override.command.scope.repository_id,
            )
        )
        if cutover_latch is not None:
            return False
        return (
            latest is not None
            and latest.override.command.kind == "disable_omission"
            and latest.override.override_id == override.command.subject_id
        )

    async def _load_active_force(
        self,
        scope: RepositoryScope,
        subject_id: str | None,
        now: datetime,
    ) -> ActiveOverride | OverrideLookupUnavailable | None:
        if subject_id is None:
            return None
        row = await self._connection.execute(
            select(operator_overrides)
            .where(
                operator_overrides.c.installation_id == scope.installation_id,
                operator_overrides.c.repository_id == scope.repository_id,
                operator_overrides.c.kind == "force_full_ci",
                operator_overrides.c.target_subject_id == subject_id,
                operator_overrides.c.expires_at > now,
            )
            .order_by(
                operator_overrides.c.applied_at.desc(),
                operator_overrides.c.override_id.desc(),
            )
            .limit(1)
        )
        mapping = row.mappings().one_or_none()
        if mapping is None:
            return None
        override = decode_operator_override_row(dict(mapping)).override
        if override.applied_at > now:
            return OverrideLookupUnavailable()
        if not override.applies_at(now):
            raise PersistenceInvariantViolation("stored force-FullCI override is not active")
        return override

    async def _load_latest_omission_control(
        self,
        scope: RepositoryScope,
        at: datetime | None,
    ) -> _SequencedOverride | None:
        statement = (
            select(operator_overrides, audit_events.c.sequence.label("_audit_sequence"))
            .select_from(
                operator_overrides.join(
                    audit_events,
                    operator_overrides.c.audit_event_id == audit_events.c.audit_event_id,
                )
            )
            .where(
                operator_overrides.c.installation_id == scope.installation_id,
                operator_overrides.c.repository_id == scope.repository_id,
                operator_overrides.c.kind.in_(("disable_omission", "enable_omission")),
            )
            .order_by(audit_events.c.sequence.desc())
            .limit(1)
        )
        if at is not None:
            statement = statement.where(operator_overrides.c.applied_at <= at)
        row = await self._connection.execute(statement)
        mapping = row.mappings().one_or_none()
        return None if mapping is None else _sequenced_override(dict(mapping))

    async def _assert_valid_enable_transition(
        self,
        scope: RepositoryScope,
        enable: _SequencedOverride,
    ) -> None:
        target_id = enable.override.command.subject_id
        if target_id is None:
            raise PersistenceInvariantViolation("stored omission enable target is missing")
        result = await self._connection.execute(
            select(operator_overrides, audit_events.c.sequence.label("_audit_sequence"))
            .select_from(
                operator_overrides.join(
                    audit_events,
                    operator_overrides.c.audit_event_id == audit_events.c.audit_event_id,
                )
            )
            .where(
                operator_overrides.c.override_id == target_id,
                operator_overrides.c.installation_id == scope.installation_id,
                operator_overrides.c.repository_id == scope.repository_id,
            )
        )
        mapping = result.mappings().one_or_none()
        if mapping is None:
            raise PersistenceInvariantViolation("stored omission enable target is missing")
        target = _sequenced_override(dict(mapping))
        if (
            target.override.override_id != target_id
            or target.override.command.kind != "disable_omission"
            or target.audit_sequence >= enable.audit_sequence
        ):
            raise PersistenceInvariantViolation("stored omission enable transition is invalid")

    async def _load_operation(self, command: OverrideCommand) -> StoredOverrideRecord | None:
        existing = await self._connection.execute(
            select(operator_overrides).where(
                operator_overrides.c.installation_id == command.scope.installation_id,
                operator_overrides.c.repository_id == command.scope.repository_id,
                operator_overrides.c.operation_id == command.operation_id,
            )
        )
        stored_row = existing.mappings().one_or_none()
        return None if stored_row is None else decode_operator_override_row(dict(stored_row))

    async def _admit_state(self) -> None:
        await admit_operator_override_state(self._connection, self._profile)

    async def _write_state(
        self,
        override: ActiveOverride,
        audit_record: AuditEventRecord,
    ) -> _StateWrite:
        row = _override_to_row(override, audit_record)
        inserted = await self._connection.scalar(
            postgres_insert(operator_overrides)
            .values(row)
            .on_conflict_do_nothing()
            .returning(operator_overrides.c.override_id)
        )
        if inserted == override.override_id:
            return "inserted"
        existing = await self._connection.execute(
            select(operator_overrides).where(
                operator_overrides.c.installation_id == override.command.scope.installation_id,
                operator_overrides.c.repository_id == override.command.scope.repository_id,
                operator_overrides.c.operation_id == override.command.operation_id,
            )
        )
        stored_row = existing.mappings().one_or_none()
        if stored_row is None:
            raise PersistenceInvariantViolation("operator override insert collision is invalid")
        stored = decode_operator_override_row(dict(stored_row))
        attempted = decode_operator_override_row(row)
        return "duplicate" if stored == attempted else "conflict"


class PostgresOperatorOverrideUnitOfWork(PostgresUnitOfWork):
    """Expose override state and audit through one compatibility-fenced transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self._operator_overrides: PostgresOperatorOverrideRepository | None = None

    @property
    def operator_overrides(self) -> PostgresOperatorOverrideRepository:
        self._require_active()
        return self._require_initialized(self._operator_overrides, "operator override repository")

    def _initialize_repositories(self) -> None:
        super()._initialize_repositories()
        connection = self._require_initialized(self._connection, "database connection")
        audit_events = self._require_initialized(self._audit_events, "audit event repository")
        self._operator_overrides = PostgresOperatorOverrideRepository(
            connection,
            audit_events,
            self._profile,
            self._require_active,
            self._mark_rollback_required,
        )


class DurableOperatorOverrideStore:
    """Long-lived adapter with atomic audit/state writes and bounded active reads."""

    def __init__(self, unit_of_work: OperatorOverrideUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def resolve_operation(self, command: OverrideCommand) -> OverrideReplayResult:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.operator_overrides.resolve_operation(command)
        except (PersistenceError, ValueError):
            return OverrideUnavailable()

    async def apply(
        self,
        override: ActiveOverride,
        audit_event: OverrideAuditEvent,
    ) -> OverrideStoreResult:
        try:
            prepared = _prepare_applied_event(override, audit_event)
            async with self._unit_of_work() as transaction:
                result = await transaction.operator_overrides.apply(override, prepared)
                if isinstance(result, OverrideConflict):
                    return result
                await transaction.commit()
                return result
        except (PersistenceError, ValueError):
            return OverrideUnavailable()

    async def record_rejection(self, audit_event: OverrideAuditEvent) -> bool:
        try:
            prepared = _prepare_rejection_event(audit_event)
            async with self._unit_of_work() as transaction:
                result = await transaction.audit_events.append(prepared)
                if isinstance(result, AuditAppendConflict):
                    return False
                await transaction.commit()
            return isinstance(result, AuditAppendAppended | AuditAppendDuplicate)
        except (PersistenceError, ValueError):
            return False

    async def resolve_active(
        self,
        *,
        scope: RepositoryScope,
        subject_id: str | None,
        now: datetime,
    ) -> OverrideLookupResult:
        try:
            async with self._unit_of_work() as transaction:
                return await transaction.operator_overrides.resolve_active(
                    scope=scope,
                    subject_id=subject_id,
                    now=now,
                )
        except (PersistenceError, ValueError):
            return OverrideLookupUnavailable()


def operator_override_state_requirements(
    profile: CompatibilityProfile,
) -> tuple[CapabilityDeclaration, ...]:
    return required_capabilities(
        profile,
        (
            AUDIT_LEDGER.declaration(),
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            OPERATOR_OVERRIDE_STATE.declaration(),
            PRODUCTION_GENERATION_CUTOVER.declaration(),
        ),
    )


async def admit_operator_override_state(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> None:
    await admit_current_capabilities(
        connection,
        profile,
        operator_override_state_requirements(profile),
    )
    if not await operator_override_state_schema_matches_contract(connection):
        raise DatabaseCapabilityUnavailable("operator override schema facts do not match")
    if not await production_cutover_schema_matches_contract(connection):
        raise DatabaseCapabilityUnavailable("production cutover latch schema facts do not match")
    if not await operator_override_runtime_principal_is_restricted(connection):
        raise DatabaseCapabilityUnavailable(
            "operator override runtime principal is not capability-restricted"
        )
    orphaned_audit = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM ci_coordinator.audit_events AS event "
            "LEFT JOIN ci_coordinator.operator_overrides AS override_state "
            "ON override_state.audit_event_id = event.audit_event_id "
            "WHERE event.event_type = convert_to('operator_override_applied', 'UTF8') "
            "AND override_state.audit_event_id IS NULL)"
        )
    )
    if orphaned_audit is not False:
        raise DatabaseCapabilityUnavailable(
            "operator override audit and state projection are inconsistent"
        )


def _require_resolution_query(
    scope: RepositoryScope,
    subject_id: str | None,
    now: datetime,
) -> None:
    if type(scope) is not RepositoryScope:
        raise ValueError("override resolution scope must be exact")
    if subject_id is not None:
        require_bounded_text(
            subject_id,
            maximum_bytes=512,
            context="operator override resolution subject",
        )
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("override resolution time must be timezone-aware")


def _sequenced_override(row: dict[str, object]) -> _SequencedOverride:
    sequence = row.get("_audit_sequence")
    if type(sequence) is not int or sequence < 1:
        raise PersistenceInvariantViolation("stored operator override audit sequence is invalid")
    return _SequencedOverride(decode_operator_override_row(row), sequence)
