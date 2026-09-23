from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.observation_gaps import (
    MAX_OBSERVATION_GAPS,
    ObservationGap,
    gap_detail_truncation_deadline,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_codec import decode_gap, encode_observation_payload
from ci_coordinator.persistence.ci_observation_lock import observation_scope_predicate
from ci_coordinator.persistence.schema import ci_observation_gaps, ci_observation_subscriptions


async def record_observation_gap(
    connection: AsyncConnection, gap: ObservationGap, now: datetime
) -> None:
    if type(gap) is not ObservationGap:
        raise TypeError("observation gap requires exact value")
    now = utc_time(now)
    if now < gap.cycle_started_at:
        raise ValueError("observation gap cannot precede its recovery cycle")
    table = ci_observation_gaps
    scope_terms = (
        table.c.installation_id == gap.scope.installation_id,
        table.c.repository_id == gap.scope.repository_id,
    )
    await connection.execute(delete(table).where(*scope_terms, table.c.expires_at <= now))
    if gap.expires_at <= now:
        return
    existing = (
        (await connection.execute(select(table).where(table.c.gap_id == gap.gap_id)))
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        if decode_gap(existing) != gap:
            raise ValueError("observation gap identity has contradictory provenance")
        return
    await connection.execute(
        insert(table).values(
            gap_id=gap.gap_id,
            installation_id=gap.scope.installation_id,
            repository_id=gap.scope.repository_id,
            gap_canonical=encode_observation_payload(gap.canonical_mapping()),
            expires_at=gap.expires_at,
        )
    )
    rows = (
        (
            await connection.execute(
                select(table)
                .where(*scope_terms)
                .order_by(table.c.expires_at, table.c.gap_id.collate("C"))
                .limit(MAX_OBSERVATION_GAPS + 2)
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > MAX_OBSERVATION_GAPS + 1:
        raise ValueError("observation gap quota was already exceeded")
    if len(rows) <= MAX_OBSERVATION_GAPS:
        return
    evicted = decode_gap(rows[0])
    subscriptions = ci_observation_subscriptions
    prior = await connection.scalar(
        select(subscriptions.c.detail_truncated_until).where(observation_scope_predicate(gap.scope))
    )
    if prior is not None and type(prior) is not datetime:
        raise ValueError("observation gap detail deadline is malformed")
    deadline = gap_detail_truncation_deadline(prior, evicted, now)
    await connection.execute(delete(table).where(table.c.gap_id == evicted.gap_id, *scope_terms))
    await connection.execute(
        update(subscriptions)
        .where(observation_scope_predicate(gap.scope))
        .values(detail_truncated_until=deadline)
    )


async def purge_expired_observation_gaps(
    connection: AsyncConnection, scope: RepositoryScope, now: datetime
) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("gap cleanup requires exact repository scope")
    now = utc_time(now)
    table = ci_observation_gaps
    await connection.execute(
        delete(table).where(
            table.c.installation_id == scope.installation_id,
            table.c.repository_id == scope.repository_id,
            table.c.expires_at <= now,
        )
    )
    subscriptions = ci_observation_subscriptions
    await connection.execute(
        update(subscriptions)
        .where(
            observation_scope_predicate(scope),
            subscriptions.c.detail_truncated_until <= now,
        )
        .values(detail_truncated_until=None)
    )
