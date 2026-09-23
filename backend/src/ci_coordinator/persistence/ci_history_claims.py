import secrets

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics._observation_values import digest
from ci_coordinator.ci_economics.history_configuration import MAX_HISTORY_DATASETS, HistoryDataset
from ci_coordinator.ci_economics.history_recent import prepare_recent_history_cycle
from ci_coordinator.ci_economics.history_scan import (
    HistoryClaim,
    HistoryScanLane,
    acquire_history_claim,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets,
    ci_history_scans,
)
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    load_history_scan,
    lock_history_scope,
    write_history_scan,
)


async def claim_history(
    connection: AsyncConnection, worker_id: str, *, lane: HistoryScanLane = "backfill"
) -> tuple[HistoryDataset, HistoryClaim] | None:
    digest(worker_id)
    if lane not in {"backfill", "discovery"}:
        raise ValueError("history claiming requires an admitted lane")
    scans, datasets = ci_history_scans, ci_history_datasets
    candidates = (
        await connection.execute(
            select(scans.c.installation_id, scans.c.repository_id)
            .select_from(
                scans.join(
                    datasets,
                    and_(
                        scans.c.installation_id == datasets.c.installation_id,
                        scans.c.repository_id == datasets.c.repository_id,
                        scans.c.generation == datasets.c.generation,
                        scans.c.configuration_revision == datasets.c.configuration_revision,
                    ),
                )
            )
            .where(
                datasets.c.state == "active",
                scans.c.lane == lane,
                or_(scans.c.traversal_complete.is_(False), scans.c.lane == "discovery"),
                scans.c.next_attempt_at <= func.statement_timestamp(),
                or_(
                    scans.c.lease_expires_at.is_(None),
                    scans.c.lease_expires_at <= func.statement_timestamp(),
                ),
            )
            .order_by(scans.c.next_attempt_at, scans.c.installation_id, scans.c.repository_id)
            .limit(MAX_HISTORY_DATASETS + 1)
        )
    ).all()
    if len(candidates) > MAX_HISTORY_DATASETS:
        raise ValueError("history dataset population exceeds its admitted bound")
    for installation_id, repository_id in candidates:
        scope = RepositoryScope(installation_id, repository_id)
        if not await lock_history_scope(connection, scope, try_only=True):
            continue
        dataset = await load_history_dataset(connection, scope, locked=True, skip_locked=True)
        if dataset is None:
            continue
        prior = await load_history_scan(connection, scope, lane=lane, locked=True, skip_locked=True)
        if prior is None:
            continue
        now = await history_database_time(connection)
        prepared = (
            prepare_recent_history_cycle(prior, now)
            if lane == "discovery" and prior.checkpoint.complete
            else prior
        )
        if prepared is None:
            continue
        acquired = acquire_history_claim(
            dataset,
            prepared,
            now=now,
            worker_id=worker_id,
            token=secrets.token_hex(32),
        )
        if acquired is None:
            continue
        successor, claim = acquired
        await write_history_scan(connection, prior, successor)
        return dataset, claim
    return None
