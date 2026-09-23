from __future__ import annotations

from typing import Literal
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.dml import Delete

from ci_coordinator.control_plane_identity.activity import ActivityPrincipal, SecurityAction
from ci_coordinator.control_plane_identity.model import ControlPlaneSessionRecord
from ci_coordinator.persistence._schema_activity import activity_diagnostic_buckets, activity_events
from ci_coordinator.persistence._schema_control_plane_identity import control_plane_sessions
from ci_coordinator.persistence.control_plane_session_codec import record_from_row
from ci_coordinator.persistence.errors import StoreUnavailable

_SESSION_DELETE_BATCH_SIZE = 128


async def lock_activity(connection: AsyncConnection) -> None:
    # Holding this lock through commit prevents keyset pages from skipping late commits.
    await connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
    await connection.execute(text("SELECT pg_catalog.pg_advisory_xact_lock(718340961520)"))


async def record_session_login(
    connection: AsyncConnection, record: ControlPlaneSessionRecord
) -> None:
    statement = insert(activity_events).values(
        occurred_at=func.statement_timestamp(),
        retain_until=func.statement_timestamp() + text("INTERVAL '2592000 seconds'"),
        issuer=record.issuer,
        subject=record.subject,
        actor=record.actor_id,
        action="login",
        outcome="committed",
        operation_ref=str(uuid4()),
    )
    try:
        await connection.execute(statement)
    except SQLAlchemyError as error:
        raise StoreUnavailable("required session activity unavailable") from error


async def delete_sessions_with_activity(
    connection: AsyncConnection,
    statement: Delete,
    action: Literal["logout", "expired", "revoked", "replaced"],
) -> int:
    if statement.table is not control_plane_sessions:
        raise ValueError("activity deletion requires the session table")
    selected = select(control_plane_sessions.c.handle_digest)
    if statement.whereclause is not None:
        selected = selected.where(statement.whereclause)
    journal_insert = insert(activity_events).values(
        occurred_at=func.statement_timestamp(),
        retain_until=func.statement_timestamp() + text("INTERVAL '2592000 seconds'"),
        action=action,
        outcome="committed",
        operation_ref=str(uuid4()),
    )
    count = 0
    try:
        # Select once: reevaluating a limited/offset selection after deletion changes its members.
        result = await connection.stream(
            selected, execution_options={"yield_per": _SESSION_DELETE_BATCH_SIZE}
        )
        try:
            async for handles in result.scalars().partitions(_SESSION_DELETE_BATCH_SIZE):
                removed = await connection.execute(
                    delete(control_plane_sessions)
                    .where(control_plane_sessions.c.handle_digest.in_(handles))
                    .returning(control_plane_sessions)
                )
                events = []
                for row in removed.mappings():
                    count += 1
                    try:
                        record = record_from_row(dict(row))
                    except (TypeError, ValueError):
                        continue
                    events.append(
                        {
                            "issuer": record.issuer,
                            "subject": record.subject,
                            "actor": record.actor_id,
                        }
                    )
                if events:
                    await connection.execute(journal_insert, events)
        finally:
            await result.close()
    except SQLAlchemyError as error:
        raise StoreUnavailable("required session activity unavailable") from error
    return count


async def record_diagnostic_count(connection: AsyncConnection, action: str) -> int:
    bucket = func.date_trunc("hour", func.statement_timestamp().op("AT TIME ZONE")("UTC")).op(
        "AT TIME ZONE"
    )("UTC")
    value = await connection.scalar(
        pg_insert(activity_diagnostic_buckets)
        .values(
            bucket=bucket,
            action=action,
            count=1,
        )
        .on_conflict_do_update(
            index_elements=["bucket", "action"],
            set_={"count": func.least(activity_diagnostic_buckets.c.count + 1, 1000000)},
        )
        .returning(activity_diagnostic_buckets.c.count)
    )
    if type(value) is not int:
        raise ValueError("invalid activity diagnostic count")
    return value


async def record_principal_diagnostic(
    connection: AsyncConnection, principal: ActivityPrincipal, action: SecurityAction
) -> None:
    if action not in ("role_denied", "export"):
        raise ValueError("invalid diagnostic action")
    count = await record_diagnostic_count(connection, action)
    if count > 128:
        return
    await connection.execute(
        insert(activity_events).values(
            occurred_at=func.statement_timestamp(),
            retain_until=func.statement_timestamp() + text("INTERVAL '2592000 seconds'"),
            issuer=principal.issuer,
            subject=principal.subject,
            actor=principal.actor_id,
            action=action,
            outcome="denied" if action == "role_denied" else "attempted",
            operation_ref=str(uuid4()),
        )
    )


async def cleanup_activity(connection: AsyncConnection) -> int:
    expired = (
        select(activity_events.c.sequence)
        .where(activity_events.c.retain_until <= func.statement_timestamp())
        .order_by(activity_events.c.retain_until, activity_events.c.sequence)
        .limit(128)
    )
    removed = (
        delete(activity_events)
        .where(activity_events.c.sequence.in_(expired))
        .returning(activity_events.c.sequence)
        .cte("activity_expired")
    )
    count = await connection.scalar(select(func.count()).select_from(removed))
    buckets = (
        select(activity_diagnostic_buckets.c.bucket, activity_diagnostic_buckets.c.action)
        .where(
            activity_diagnostic_buckets.c.bucket
            <= func.statement_timestamp() - text("INTERVAL '172800 seconds'")
        )
        .order_by(activity_diagnostic_buckets.c.bucket, activity_diagnostic_buckets.c.action)
        .limit(128)
    )
    await connection.execute(
        delete(activity_diagnostic_buckets).where(
            tuple_(activity_diagnostic_buckets.c.bucket, activity_diagnostic_buckets.c.action).in_(
                buckets
            )
        )
    )
    if type(count) is not int:
        raise ValueError("invalid activity cleanup count")
    return count
