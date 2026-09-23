from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.observation import MAX_OBSERVATION_REPOSITORIES
from ci_coordinator.ci_economics.observation_gaps import (
    MAX_OBSERVATION_GAP_PAGE_SIZE,
    InvalidObservationGapCursor,
    ObservationGapCursor,
)
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.ci_economics.sources import MAX_PROVIDER_SOURCES_PER_REPOSITORY
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_codec import decode_gap, decode_subscription
from ci_coordinator.persistence.ci_observation_gaps import purge_expired_observation_gaps
from ci_coordinator.persistence.ci_observation_lock import (
    load_locked_subscription,
    lock_observation_scope,
    observation_database_time,
)
from ci_coordinator.persistence.ci_observation_scan_state import load_observation_scans
from ci_coordinator.persistence.schema import (
    ci_observation_gaps,
    ci_observation_subscriptions,
    ci_workflow_attempt_collections,
)


async def observation_status(
    connection: AsyncConnection, scope: RepositoryScope
) -> ObservationStatus:
    await lock_observation_scope(connection, scope)
    row = await load_locked_subscription(connection, scope)
    snapshot = None if row is None else decode_subscription(row)
    scans = () if snapshot is None else await load_observation_scans(connection, snapshot)
    sources = ci_workflow_attempt_collections
    population = (
        select(sources.c.subject_id)
        .where(
            sources.c.installation_id == scope.installation_id,
            sources.c.repository_id == scope.repository_id,
            sources.c.source_kind == "provider_run",
        )
        .limit(MAX_PROVIDER_SOURCES_PER_REPOSITORY + 1)
        .subquery()
    )
    occupied = await connection.scalar(select(func.count()).select_from(population))
    if type(occupied) is not int:
        raise ValueError("observation source occupancy unavailable")
    now = await observation_database_time(connection)
    truncated_until = None if row is None else row["detail_truncated_until"]
    if truncated_until is not None and type(truncated_until) is not datetime:
        raise ValueError("observation truncation deadline malformed")
    return ObservationStatus(
        scope,
        snapshot,
        scans,
        occupied,
        truncated_until if truncated_until is not None and truncated_until > now else None,
        now,
    )


async def observation_gaps(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    after_cursor: str | None,
    limit: int,
) -> ObservationGapPage:
    if type(scope) is not RepositoryScope:
        raise TypeError("observation gaps require exact repository scope")
    if type(limit) is not int or not 1 <= limit <= MAX_OBSERVATION_GAP_PAGE_SIZE:
        raise ValueError("observation gap page limit must be between one and fifty")
    cursor = None if after_cursor is None else ObservationGapCursor.parse(after_cursor)
    await lock_observation_scope(connection, scope)
    subscription = await load_locked_subscription(connection, scope)
    snapshot = None if subscription is None else decode_subscription(subscription)
    now = await observation_database_time(connection)
    table = ci_observation_gaps
    query = select(table).where(
        table.c.installation_id == scope.installation_id,
        table.c.repository_id == scope.repository_id,
        table.c.expires_at > now,
    )
    if cursor is not None:
        if (
            cursor.scope != scope
            or snapshot is None
            or cursor.config_revision != snapshot.revision
            or (await connection.execute(query.where(table.c.gap_id == cursor.gap_id))).first()
            is None
        ):
            raise InvalidObservationGapCursor("gap continuation requires a fresh first page")
        query = query.where(table.c.gap_id.collate("C") > cursor.gap_id)
    rows = (
        (await connection.execute(query.order_by(table.c.gap_id.collate("C")).limit(limit + 1)))
        .mappings()
        .all()
    )
    gaps = tuple(decode_gap(row) for row in rows[:limit])
    next_cursor = None
    if len(rows) > limit:
        if snapshot is None:
            raise ValueError("retained gaps require a current observation subscription")
        next_cursor = ObservationGapCursor(scope, snapshot.revision, gaps[-1].gap_id).value
    return ObservationGapPage(scope, gaps, next_cursor, now)


async def purge_observation_gaps(connection: AsyncConnection, *, scope_limit: int) -> int:
    if type(scope_limit) is not int or not 1 <= scope_limit <= 4:
        raise ValueError("observation cleanup handles one to four scopes")
    subscriptions = ci_observation_subscriptions
    gaps = ci_observation_gaps
    earliest_gap = (
        select(func.min(gaps.c.expires_at))
        .where(
            gaps.c.installation_id == subscriptions.c.installation_id,
            gaps.c.repository_id == subscriptions.c.repository_id,
        )
        .correlate(subscriptions)
        .scalar_subquery()
    )
    due_at = func.least(earliest_gap, subscriptions.c.detail_truncated_until)
    candidates = (
        await connection.execute(
            select(
                subscriptions.c.installation_id,
                subscriptions.c.repository_id,
            )
            .where(due_at.is_not(None), due_at <= func.statement_timestamp())
            .order_by(
                due_at,
                subscriptions.c.installation_id,
                subscriptions.c.repository_id,
            )
            .limit(MAX_OBSERVATION_REPOSITORIES + 1)
        )
    ).all()
    if len(candidates) > MAX_OBSERVATION_REPOSITORIES:
        raise ValueError("observation configuration quota exceeded")
    cleaned = 0
    for installation_id, repository_id in candidates:
        scope = RepositoryScope(installation_id, repository_id)
        if not await lock_observation_scope(connection, scope, try_only=True):
            continue
        if await load_locked_subscription(connection, scope, skip_locked=True) is None:
            continue
        await purge_expired_observation_gaps(
            connection, scope, await observation_database_time(connection)
        )
        cleaned += 1
        if cleaned == scope_limit:
            break
    return cleaned
