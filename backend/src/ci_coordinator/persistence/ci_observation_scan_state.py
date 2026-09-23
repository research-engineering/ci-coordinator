from datetime import datetime

from sqlalchemy import DateTime, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.observation import ObservationSnapshot
from ci_coordinator.ci_economics.observation_progress import ObservationScanProgress
from ci_coordinator.ci_economics.observation_scan import (
    ObservationLane,
    ObservationLease,
    ObservationScanState,
)
from ci_coordinator.persistence.ci_observation_codec import decode_scan, encode_scan
from ci_coordinator.persistence.ci_observation_lock import observation_scope_predicate
from ci_coordinator.persistence.schema import ci_observation_scans, ci_observation_subscriptions


class ObservationLeaseExpired(RuntimeError):
    pass


async def load_observation_scans(
    connection: AsyncConnection, snapshot: ObservationSnapshot
) -> tuple[ObservationScanProgress, ObservationScanProgress]:
    table = ci_observation_scans
    rows = (
        (
            await connection.execute(
                select(table)
                .where(
                    table.c.installation_id == snapshot.scope.installation_id,
                    table.c.repository_id == snapshot.scope.repository_id,
                )
                .order_by(table.c.lane)
                .with_for_update()
                .limit(3)
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 2:
        raise ValueError("observation requires exactly two fixed scan lanes")
    first, second = (decode_scan(row) for row in rows)
    if (first.state.lane, second.state.lane) != ("backfill", "recent") or any(
        item.state.scope != snapshot.scope or item.state.config_revision != snapshot.revision
        for item in (first, second)
    ):
        raise ValueError("observation scan lanes contradict current configuration")
    return first, second


async def write_observation_scan(
    connection: AsyncConnection,
    prior: ObservationScanState,
    successor: ObservationScanProgress,
    *,
    live_lease: ObservationLease | None = None,
) -> None:
    state = successor.state
    if (
        state.scope != prior.scope
        or state.lane != prior.lane
        or state.config_revision != prior.config_revision
        or state.revision <= prior.revision
    ):
        raise ValueError("observation scan transition changes its immutable identity")
    table = ci_observation_scans
    values = encode_scan(successor)
    for name in ("installation_id", "repository_id", "lane", "config_revision"):
        del values[name]
    statement = update(table)
    if live_lease is not None:
        interval = func.tstzrange(
            literal(live_lease.acquired_at, DateTime(timezone=True)),
            literal(live_lease.expires_at, DateTime(timezone=True)),
            "[)",
        )
        statement = statement.where(interval.op("@>")(func.clock_timestamp()))
    revision = await connection.scalar(
        statement.where(
            table.c.installation_id == prior.scope.installation_id,
            table.c.repository_id == prior.scope.repository_id,
            table.c.lane == prior.lane,
            table.c.config_revision == prior.config_revision,
            table.c.revision == prior.revision,
        )
        .values(**values)
        .returning(table.c.revision)
    )
    if revision != state.revision:
        if live_lease is not None:
            raise ObservationLeaseExpired("observation transition lost its live lease")
        raise ValueError("observation scan CAS lost its locked revision")


async def schedule_observation_scope(
    connection: AsyncConnection,
    snapshot: ObservationSnapshot,
    scans: tuple[ObservationScanProgress, ObservationScanProgress],
    replacement: ObservationScanProgress,
    preferred_lane: ObservationLane,
) -> None:
    updated = tuple(
        replacement if value.state.lane == replacement.state.lane else value for value in scans
    )
    next_attempt_at = min(_eligible_at(value.state) for value in updated)
    table = ci_observation_subscriptions
    revision = await connection.scalar(
        update(table)
        .where(
            observation_scope_predicate(snapshot.scope),
            table.c.revision == snapshot.revision,
        )
        .values(next_attempt_at=next_attempt_at, preferred_lane=preferred_lane)
        .returning(table.c.revision)
    )
    if revision != snapshot.revision:
        raise ValueError("observation scheduling lost its locked configuration")


def _eligible_at(state: ObservationScanState) -> datetime:
    return (
        state.next_attempt_at
        if state.lease is None
        else max(state.next_attempt_at, state.lease.expires_at)
    )
