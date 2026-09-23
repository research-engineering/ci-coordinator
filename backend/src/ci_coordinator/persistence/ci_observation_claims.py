import secrets
from dataclasses import replace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics._observation_values import digest
from ci_coordinator.ci_economics.observation import MAX_OBSERVATION_REPOSITORIES
from ci_coordinator.ci_economics.observation_ports import ClaimedObservation
from ci_coordinator.ci_economics.observation_scan import acquire_observation_claim
from ci_coordinator.ci_economics.observation_schedule import prepare_observation_cycle
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_codec import decode_subscription
from ci_coordinator.persistence.ci_observation_gaps import record_observation_gap
from ci_coordinator.persistence.ci_observation_lock import (
    load_locked_subscription,
    lock_observation_scope,
    observation_database_time,
)
from ci_coordinator.persistence.ci_observation_scan_state import (
    load_observation_scans,
    schedule_observation_scope,
    write_observation_scan,
)
from ci_coordinator.persistence.schema import ci_observation_subscriptions


async def claim_observation(
    connection: AsyncConnection, worker_id: str
) -> ClaimedObservation | None:
    digest(worker_id)
    table = ci_observation_subscriptions
    candidates = (
        await connection.execute(
            select(table.c.installation_id, table.c.repository_id)
            .where(
                table.c.enabled.is_(True),
                table.c.next_attempt_at <= func.statement_timestamp(),
            )
            .order_by(table.c.next_attempt_at, table.c.installation_id, table.c.repository_id)
            .limit(MAX_OBSERVATION_REPOSITORIES + 1)
        )
    ).all()
    if len(candidates) > MAX_OBSERVATION_REPOSITORIES:
        raise ValueError("observation configuration quota exceeded")
    for installation_id, repository_id in candidates:
        scope = RepositoryScope(installation_id, repository_id)
        if not await lock_observation_scope(connection, scope, try_only=True):
            continue
        row = await load_locked_subscription(connection, scope, skip_locked=True)
        if row is None:
            continue
        snapshot = decode_subscription(row)
        if not snapshot.configuration.enabled:
            continue
        scans = await load_observation_scans(connection, snapshot)
        preferred = row["preferred_lane"]
        if preferred not in {"recent", "backfill"}:
            raise ValueError("observation preferred lane is malformed")
        now = await observation_database_time(connection)
        for progress in sorted(scans, key=lambda scan: scan.state.lane != preferred):
            prepared = prepare_observation_cycle(
                snapshot,
                progress.state,
                last_completed_through=progress.last_completed_through,
                now=now,
            )
            if prepared is None:
                continue
            acquired = acquire_observation_claim(
                snapshot,
                prepared.state,
                now=now,
                worker_id=worker_id,
                token=secrets.token_hex(32),
            )
            if acquired is None:
                continue
            state, claim = acquired
            successor = replace(progress, state=state)
            if prepared.gap is not None:
                await record_observation_gap(connection, prepared.gap, now)
            await write_observation_scan(
                connection,
                progress.state,
                successor,
                live_lease=claim.lease,
            )
            await schedule_observation_scope(
                connection,
                snapshot,
                scans,
                successor,
                "backfill" if state.lane == "recent" else "recent",
            )
            return ClaimedObservation(snapshot, claim)
    return None
