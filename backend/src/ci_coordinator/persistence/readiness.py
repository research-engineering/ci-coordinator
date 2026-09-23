from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.audit_replay import (
    AuditEventRecord,
    verify_audit_chain_extension,
)
from ci_coordinator.persistence.audit_repository import (
    load_audit_records_after as load_audit_records_after,
)
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.connection import configure_read_committed, verify_read_committed
from ci_coordinator.persistence.errors import (
    DatabaseCapabilityUnavailable,
    DatabaseCompatibilityError,
    PersistenceError,
)
from ci_coordinator.persistence.schema import audit_events, audit_ledger_head
from ci_coordinator.persistence.schema_capabilities import audit_ledger_requirements

MAXIMUM_DATABASE_READINESS_WAITERS = 16


@dataclass(frozen=True)
class DatabaseReadiness:
    ready: bool
    reason: str
    verified_revision: int | None = None


@dataclass(frozen=True)
class _LedgerHead:
    revision: int
    event_hash: str | None


class DatabaseReadinessProbe:
    """Verify one immutable ledger prefix and only its later extensions."""

    def __init__(
        self,
        engine: AsyncEngine,
        alembic_config_path: Path,
        *,
        timeout_ms: int = 5_000,
    ) -> None:
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise ValueError("readiness timeout must be positive milliseconds")
        self._engine = engine
        self._alembic_config_path = alembic_config_path
        self._timeout_ms = timeout_ms
        self._inflight: asyncio.Task[tuple[DatabaseReadiness, AuditEventRecord | None]] | None = (
            None
        )
        self._active_waiters = 0
        self._verified = False
        self._last_record: AuditEventRecord | None = None

    async def check(self) -> DatabaseReadiness:
        if self._active_waiters >= MAXIMUM_DATABASE_READINESS_WAITERS:
            return DatabaseReadiness(False, "readiness_overloaded")
        self._active_waiters += 1
        try:
            task = self._inflight
            if task is None or task.done():
                task = asyncio.create_task(self._check_and_update())
                self._inflight = task
            try:
                async with asyncio.timeout(self._timeout_ms / 1_000):
                    result, _last_record = await asyncio.shield(task)
                    return result
            except TimeoutError:
                return DatabaseReadiness(False, "readiness_timeout")
        finally:
            self._active_waiters -= 1

    async def _check_and_update(
        self,
    ) -> tuple[DatabaseReadiness, AuditEventRecord | None]:
        result, last_record = await _check_database_readiness(
            self._engine,
            self._alembic_config_path,
            timeout_ms=self._timeout_ms,
            prefix_verified=self._verified,
            previous_record=self._last_record,
        )
        if result.ready or result.reason == "audit_verification_in_progress":
            self._verified = True
            self._last_record = last_record
            result = DatabaseReadiness(
                result.ready,
                result.reason,
                0 if last_record is None else last_record.sequence,
            )
        elif result.reason not in {
            "readiness_timeout",
            "store_unavailable_or_invalid",
            "database_capability_unavailable",
        }:
            self._verified = False
            self._last_record = None
        return result, last_record


def bundled_alembic_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "alembic.ini"


def migration_heads(alembic_config_path: Path) -> tuple[str, ...]:
    config = Config(str(alembic_config_path))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


async def check_database_readiness(
    engine: AsyncEngine,
    alembic_config_path: Path,
    *,
    timeout_ms: int = 5_000,
) -> DatabaseReadiness:
    return await DatabaseReadinessProbe(
        engine,
        alembic_config_path,
        timeout_ms=timeout_ms,
    ).check()


async def _check_database_readiness(
    engine: AsyncEngine,
    alembic_config_path: Path,
    *,
    timeout_ms: int,
    prefix_verified: bool,
    previous_record: AuditEventRecord | None,
) -> tuple[DatabaseReadiness, AuditEventRecord | None]:
    try:
        code_heads = migration_heads(alembic_config_path)
    except Exception:
        return DatabaseReadiness(False, "code_migration_unavailable"), None
    if len(code_heads) != 1:
        return DatabaseReadiness(False, "code_migration_heads_not_unique"), None
    profile = load_bundled_profile()
    try:
        async with asyncio.timeout(timeout_ms / 1_000):
            async with engine.connect() as raw_connection:
                connection = await configure_read_committed(raw_connection, profile)
                async with connection.begin():
                    await verify_read_committed(connection, profile)
                    await acquire_compatibility_fence(
                        connection,
                        profile,
                        CompatibilityFenceMode.PARTICIPANT,
                    )
                    head_row = await _load_head_row(connection)
                    if head_row is None:
                        return DatabaseReadiness(False, "database_capability_unavailable"), None
                    await admit_schema_dependent_operation(
                        connection,
                        profile,
                        audit_ledger_requirements(profile),
                    )
                    head = _decode_head(dict(head_row))
                    if head is None:
                        return DatabaseReadiness(False, "audit_head_invalid"), None
                    checkpoint_sequence = (
                        previous_record.sequence if previous_record is not None else 0
                    )
                    checkpoint_hash = (
                        previous_record.event_hash if previous_record is not None else None
                    )
                    if prefix_verified and head.revision == checkpoint_sequence:
                        if head.event_hash != checkpoint_hash:
                            return DatabaseReadiness(False, "audit_head_invalid"), None
                        return DatabaseReadiness(True, "ready"), previous_record
                    if prefix_verified and head.revision < checkpoint_sequence:
                        return DatabaseReadiness(False, "audit_head_invalid"), None
                    records = await load_audit_records_after(
                        connection,
                        checkpoint_sequence,
                        through_sequence=head.revision,
                        limit=READINESS_AUDIT_BATCH_SIZE,
                    )
                    verification = verify_audit_chain_extension(previous_record, records)
                    if not verification.valid:
                        return DatabaseReadiness(False, "audit_chain_invalid"), None
                    last_record = records[-1] if records else previous_record
                    verified_sequence = last_record.sequence if last_record is not None else 0
                    if verified_sequence < head.revision:
                        if not records:
                            return DatabaseReadiness(False, "audit_head_invalid"), None
                        return (
                            DatabaseReadiness(False, "audit_verification_in_progress"),
                            last_record,
                        )
                    if not _head_matches_record(head, last_record):
                        return DatabaseReadiness(False, "audit_head_invalid"), None
                    current_head_row = await _load_head_row(
                        connection, include_maximum_sequence=True
                    )
                    if current_head_row is None:
                        return DatabaseReadiness(False, "database_capability_unavailable"), None
                    current_head = _decode_head(dict(current_head_row))
                    if current_head is None:
                        return DatabaseReadiness(False, "audit_head_invalid"), None
                    if current_head != head:
                        return (
                            DatabaseReadiness(False, "audit_verification_in_progress"),
                            last_record,
                        )
                    maximum_sequence = current_head_row["maximum_sequence"]
                    if maximum_sequence != (head.revision or None):
                        return DatabaseReadiness(False, "audit_head_invalid"), None
    except TimeoutError:
        return DatabaseReadiness(False, "readiness_timeout"), None
    except DatabaseCapabilityUnavailable:
        return DatabaseReadiness(False, "database_capability_unavailable"), None
    except DatabaseCompatibilityError:
        return DatabaseReadiness(False, "database_compatibility_invalid"), None
    except (SQLAlchemyError, PersistenceError):
        return DatabaseReadiness(False, "store_unavailable_or_invalid"), None
    return DatabaseReadiness(True, "ready"), last_record


async def _load_head_row(
    connection: AsyncConnection, *, include_maximum_sequence: bool = False
) -> RowMapping | None:
    statement = select(
        audit_ledger_head.c.revision,
        audit_ledger_head.c.last_sequence,
        audit_ledger_head.c.last_event_hash,
    ).where(audit_ledger_head.c.head_id == 1)
    if include_maximum_sequence:
        statement = statement.add_columns(
            select(func.max(audit_events.c.sequence)).scalar_subquery().label("maximum_sequence")
        )
    result = await connection.execute(statement)
    return result.mappings().one_or_none()


def _decode_head(row: dict[str, object]) -> _LedgerHead | None:
    revision = row["revision"]
    last_sequence = row["last_sequence"]
    last_event_hash = row["last_event_hash"]
    if type(revision) is not int or revision < 0:
        return None
    if revision == 0:
        return _LedgerHead(0, None) if last_sequence is None and last_event_hash is None else None
    if type(last_sequence) is not int or last_sequence != revision:
        return None
    stored_hash = (
        last_event_hash.tobytes() if isinstance(last_event_hash, memoryview) else last_event_hash
    )
    if type(stored_hash) is not bytes or len(stored_hash) != 32:
        return None
    return _LedgerHead(revision, stored_hash.hex())


def _head_matches_record(
    head: _LedgerHead,
    record: AuditEventRecord | None,
) -> bool:
    if record is None:
        return head == _LedgerHead(0, None)
    return head.revision == record.sequence and head.event_hash == record.event_hash


READINESS_AUDIT_BATCH_SIZE = 4_096
