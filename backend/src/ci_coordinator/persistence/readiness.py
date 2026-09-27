from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
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
        managed: bool = False,
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
        self._active = not managed
        self._stopped = False
        self._connection_finalizer: asyncio.Task[None] | None = None
        self._close_failed = False

    def activate(self) -> None:
        if self._stopped:
            raise RuntimeError("database readiness cannot be restarted")
        self._active = True

    def stop(self) -> None:
        if self._stopped:
            return
        self._active = False
        self._stopped = True
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()

    async def drain(self) -> None:
        self.stop()
        task = self._inflight
        cancellation: asyncio.CancelledError | None = None
        if task is not None:
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError as error:
                    cancellation = cancellation or error
            if not task.cancelled():
                task.exception()
        if self._close_failed:
            if cancellation is None:
                raise RuntimeError("database readiness cleanup failed") from None
            cancellation.add_note("database readiness cleanup also failed")
        if cancellation is not None:
            raise cancellation

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[AsyncConnection]:
        connection = await self._engine.connect()
        primary_error: BaseException | None = None
        try:
            yield connection
        except BaseException as error:
            primary_error = error
            raise
        finally:
            finalizer = asyncio.create_task(connection.close())
            self._connection_finalizer = finalizer
            cancellation: asyncio.CancelledError | None = None
            while not finalizer.done():
                try:
                    await asyncio.wait({finalizer})
                except asyncio.CancelledError as error:
                    cancellation = cancellation or error
            if finalizer.cancelled() or finalizer.exception() is not None:
                self._close_failed = True
                if primary_error is not None:
                    primary_error.add_note("database readiness connection cleanup also failed")
                elif cancellation is not None:
                    cancellation.add_note("database readiness connection cleanup also failed")
                else:
                    raise SQLAlchemyError("readiness connection cleanup failed") from None
            if cancellation is not None:
                if primary_error is None:
                    raise cancellation
                primary_error.add_note("additional cancellation during database readiness cleanup")

    async def check(self) -> DatabaseReadiness:
        if not self._active:
            return DatabaseReadiness(False, "readiness_stopped")
        if self._close_failed:
            return DatabaseReadiness(False, "store_unavailable_or_invalid")
        if self._active_waiters >= MAXIMUM_DATABASE_READINESS_WAITERS:
            return DatabaseReadiness(False, "readiness_overloaded")
        self._active_waiters += 1
        try:
            task = self._inflight
            if task is None or task.done():
                deadline = asyncio.get_running_loop().time() + self._timeout_ms / 1_000
                task = asyncio.create_task(self._check_and_update(deadline))
                self._inflight = task
            try:
                async with asyncio.timeout(self._timeout_ms / 1_000):
                    result, _last_record = await asyncio.shield(task)
                    if not self._active:
                        return DatabaseReadiness(False, "readiness_stopped")
                    return result
            except TimeoutError:
                return DatabaseReadiness(False, "readiness_timeout")
        finally:
            self._active_waiters -= 1

    async def _check_and_update(
        self,
        deadline: float,
    ) -> tuple[DatabaseReadiness, AuditEventRecord | None]:
        prefix_verified, previous_record = self._verified, self._last_record
        timeout = asyncio.timeout_at(deadline)
        try:
            async with timeout:
                result, last_record = await _check_database_readiness(
                    self._connect,
                    self._alembic_config_path,
                    prefix_verified=prefix_verified,
                    previous_record=previous_record,
                )
        except TimeoutError:
            return DatabaseReadiness(False, "readiness_timeout"), None
        if (
            not self._active
            or asyncio.current_task() is not self._inflight
            or self._verified != prefix_verified
            or self._last_record is not previous_record
        ):
            return DatabaseReadiness(False, "readiness_stopped"), None
        if timeout.expired() or asyncio.get_running_loop().time() >= deadline:
            return DatabaseReadiness(False, "readiness_timeout"), None
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
    connect: Callable[[], AbstractAsyncContextManager[AsyncConnection]],
    alembic_config_path: Path,
    *,
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
        async with connect() as raw_connection:
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
                checkpoint_sequence = previous_record.sequence if previous_record is not None else 0
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
                current_head_row = await _load_head_row(connection, include_maximum_sequence=True)
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
