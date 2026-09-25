from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.control_plane_identity.activity import (
    ActivityPrincipal,
    ActivityUnavailable,
    LoginDiagnostic,
)
from ci_coordinator.control_plane_identity.activity_cursor import (
    ActivityCursorCodec,
    ActivityPosition,
)
from ci_coordinator.control_plane_identity.activity_query import (
    BUSINESS_ACTIONS,
    SECURITY_RETENTION_SECONDS,
    ActivityEntry,
    ActivityOutcome,
    ActivityPage,
    ActivityQuery,
    is_activity_actor,
)
from ci_coordinator.kernel import NoQueueAdmission
from ci_coordinator.persistence._schema_activity import activity_events
from ci_coordinator.persistence._schema_audit import audit_events
from ci_coordinator.persistence.activity_write import (
    cleanup_activity,
    cleanup_diagnostic_buckets,
    lock_activity,
    record_diagnostic_count,
    record_principal_diagnostic,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.control_plane_session_repository import _identity_transaction
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.schema_capabilities import (
    AUDIT_LEDGER,
    control_plane_identity_state_requirements,
)


class PostgresActivityStore:
    def __init__(self, engine: AsyncEngine, cursor_codec: ActivityCursorCodec) -> None:
        self._engine = engine
        self._codec = cursor_codec
        self._profile = load_bundled_profile()
        self._required = control_plane_identity_state_requirements(self._profile)
        self._reads = NoQueueAdmission(4)
        self._diagnostics = NoQueueAdmission(2)

    async def page(
        self, query: ActivityQuery, principal: ActivityPrincipal, cursor: str | None
    ) -> ActivityPage:
        lease = self._reads.try_acquire()
        if lease is None:
            raise ActivityUnavailable("activity read admission exhausted")
        try:
            required = self._required
            if query.source == "business":
                required = tuple(sorted({*required, AUDIT_LEDGER.declaration()}))
            async with (
                asyncio.timeout(3),
                _identity_transaction(self._engine, self._profile, required) as connection,
            ):
                await connection.execute(text("SET LOCAL statement_timeout = '2s'"))
                now = await _database_time(connection)
                if principal.expires_at <= now:
                    raise ActivityUnavailable("activity principal expired")
                position = (
                    None
                    if cursor is None
                    else self._codec.decode(cursor, query, principal, now=now)
                )
                table = activity_events if query.source == "security" else audit_events
                watermark = select(table.c.sequence).order_by(table.c.sequence.desc()).limit(1)
                if query.source == "security":
                    watermark = watermark.where(table.c.issuer == query.issuer)
                else:
                    if query.scope is None:
                        raise ValueError("missing activity repository scope")
                    watermark = watermark.where(
                        table.c.installation_id == query.scope.installation_id,
                        table.c.repository_id == query.scope.repository_id,
                    )
                through = (
                    await connection.scalar(watermark) if position is None else position.through
                )
                if through is None:
                    through = 0
                if type(through) is not int:
                    raise ActivityUnavailable("invalid activity watermark")
                before = None if position is None else position.before
                try:
                    rows = await _read_entries(connection, query, now, through, before)
                except ValueError as error:
                    raise ActivityUnavailable("invalid retained activity") from error
                items = rows[: query.limit]
                next_cursor = None
                if len(rows) > query.limit:
                    expiry = (
                        min(now + timedelta(minutes=5), principal.expires_at)
                        if position is None
                        else position.expires_at
                    )
                    next_cursor = self._codec.encode(
                        query, principal, ActivityPosition(through, items[-1].sequence, expiry)
                    )
                return ActivityPage(
                    items,
                    next_cursor,
                    now,
                    SECURITY_RETENTION_SECONDS if query.source == "security" else None,
                    "journal_transaction" if query.source == "security" else "audit_reference_only",
                )
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, UnicodeError, TimeoutError) as error:
            raise ActivityUnavailable("activity read unavailable") from error
        finally:
            lease.release()

    async def login_diagnostic(self, action: LoginDiagnostic) -> None:
        if action not in ("login_rejected", "login_unavailable"):
            raise ValueError("invalid login diagnostic")
        await self._diagnostic(action, None)

    async def principal_diagnostic(
        self, principal: ActivityPrincipal, action: Literal["role_denied", "export"]
    ) -> None:
        await self._diagnostic(action, principal)

    async def _diagnostic(self, action: str, principal: ActivityPrincipal | None) -> None:
        lease = self._diagnostics.try_acquire()
        if lease is None:
            return
        try:
            async with (
                asyncio.timeout(0.5),
                _identity_transaction(self._engine, self._profile, self._required) as connection,
            ):
                await connection.execute(text("SET LOCAL statement_timeout = '250ms'"))
                await connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                if principal is None:
                    await cleanup_diagnostic_buckets(connection)
                    await record_diagnostic_count(connection, action)
                elif action in ("role_denied", "export"):
                    await lock_activity(connection)
                    await cleanup_activity(connection)
                    await record_principal_diagnostic(
                        connection, principal, cast(Literal["role_denied", "export"], action)
                    )
                else:
                    raise ValueError("invalid principal diagnostic")
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError, TimeoutError) as error:
            raise ActivityUnavailable("activity diagnostic unavailable") from error
        finally:
            lease.release()

    async def cleanup(self) -> int:
        try:
            async with (
                asyncio.timeout(3),
                _identity_transaction(self._engine, self._profile, self._required) as connection,
            ):
                await connection.execute(text("SET LOCAL statement_timeout = '2s'"))
                await lock_activity(connection)
                return await cleanup_activity(connection)
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError, TimeoutError) as error:
            raise ActivityUnavailable("activity cleanup unavailable") from error


async def _database_time(connection: AsyncConnection) -> datetime:
    value = await connection.scalar(select(func.statement_timestamp()))
    if type(value) is not datetime or value.tzinfo is None:
        raise ActivityUnavailable("invalid activity clock")
    return value.astimezone(UTC)


async def _read_entries(
    connection: AsyncConnection,
    query: ActivityQuery,
    now: datetime,
    through: int,
    before: int | None,
) -> tuple[ActivityEntry, ...]:
    if query.source == "security":
        table = activity_events
        predicates = [
            table.c.issuer == query.issuer,
            table.c.retain_until > now,
            table.c.occurred_at >= query.since,
            table.c.occurred_at < min(query.until, now),
        ]
        statement = select(table)
        action = table.c.action
        actor = table.c.actor
    else:
        if query.scope is None:
            raise ValueError("activity scope required")
        table = audit_events
        predicates = [
            table.c.installation_id == query.scope.installation_id,
            table.c.repository_id == query.scope.repository_id,
            table.c.event_type.in_([value.encode("ascii") for value in BUSINESS_ACTIONS]),
            table.c.created_at >= _audit_time(query.since),
            table.c.created_at < _audit_time(query.until),
            table.c.created_at <= _audit_time(now),
        ]
        statement = select(
            table.c.sequence,
            table.c.created_at,
            table.c.actor,
            table.c.event_type,
            table.c.idempotency_key_digest,
            table.c.audit_event_id,
            table.c.event_hash,
        )
        action = table.c.event_type
        actor = table.c.actor
    predicates.append(table.c.sequence <= through)
    if before is not None:
        predicates.append(table.c.sequence < before)
    if query.action is not None:
        predicates.append(
            action == (query.action if query.source == "security" else query.action.encode("ascii"))
        )
    if query.actor is not None:
        predicates.append(
            actor == (query.actor if query.source == "security" else query.actor.encode("ascii"))
        )
    result = await connection.execute(
        statement.where(*predicates).order_by(table.c.sequence.desc()).limit(query.limit + 1)
    )
    entries: list[ActivityEntry] = []
    for row in result.mappings():
        if query.source == "security":
            entries.append(
                ActivityEntry(
                    sequence=row["sequence"],
                    source="security",
                    action=row["action"],
                    outcome=cast(ActivityOutcome, row["outcome"]),
                    occurred_at=row["occurred_at"],
                    actor=row["actor"],
                    issuer=row["issuer"],
                    subject=row["subject"],
                    operation_ref=row["operation_ref"],
                )
            )
        else:
            actor_text = bytes(row["actor"]).decode("utf-8")
            entries.append(
                ActivityEntry(
                    sequence=row["sequence"],
                    source="business",
                    action=bytes(row["event_type"]).decode("ascii"),
                    outcome="committed",
                    occurred_at=datetime.fromisoformat(row["created_at"]),
                    actor=actor_text if is_activity_actor(actor_text) else None,
                    issuer=None,
                    subject=None,
                    operation_ref="sha256:" + bytes(row["idempotency_key_digest"]).hex(),
                    audit_event_id=row["audit_event_id"],
                    event_hash=bytes(row["event_hash"]).hex(),
                )
            )
    return tuple(entries)


def _audit_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
