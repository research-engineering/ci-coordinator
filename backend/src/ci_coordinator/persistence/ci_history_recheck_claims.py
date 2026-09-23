import secrets

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics._observation_values import digest
from ci_coordinator.ci_economics.history_configuration import MAX_HISTORY_DATASETS
from ci_coordinator.ci_economics.history_ports import ClaimedHistoryRecheck
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_ACQUISITIONS,
    HistoryRecheckSource,
    acquire_history_recheck,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets as datasets,
)
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_rechecks as rechecks,
)
from ci_coordinator.persistence.ci_history_recheck_codec import decode_history_recheck
from ci_coordinator.persistence.ci_history_recheck_completion import recover_recheck_exhaustion
from ci_coordinator.persistence.ci_history_recheck_rows import write_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
)


async def claim_history_recheck(
    connection: AsyncConnection, worker_id: str, source: HistoryRecheckSource
) -> ClaimedHistoryRecheck:
    digest(worker_id)
    if source not in {"recent", "repair"}:
        raise ValueError("history claim requires a known queue source")
    due = (
        rechecks.c.source == source,
        rechecks.c.next_attempt_at <= func.statement_timestamp(),
        or_(
            rechecks.c.lease_expires_at.is_(None),
            rechecks.c.lease_expires_at <= func.statement_timestamp(),
        ),
    )
    candidates = (
        await connection.execute(
            select(
                rechecks.c.installation_id,
                rechecks.c.repository_id,
                func.min(rechecks.c.next_attempt_at).label("due_at"),
            )
            .select_from(
                rechecks.join(
                    datasets,
                    and_(
                        rechecks.c.installation_id == datasets.c.installation_id,
                        rechecks.c.repository_id == datasets.c.repository_id,
                        rechecks.c.generation == datasets.c.generation,
                    ),
                )
            )
            .where(datasets.c.state == "active", *due)
            .group_by(rechecks.c.installation_id, rechecks.c.repository_id)
            .order_by("due_at", rechecks.c.installation_id, rechecks.c.repository_id)
            .limit(MAX_HISTORY_DATASETS + 1)
        )
    ).all()
    if len(candidates) > MAX_HISTORY_DATASETS:
        raise ValueError("history candidate scopes exceed their admitted bound")
    capacity = False
    for installation_id, repository_id, _ in candidates:
        scope = RepositoryScope(installation_id, repository_id)
        if not await lock_history_scope(connection, scope, try_only=True):
            continue
        dataset = await load_history_dataset(connection, scope, locked=True, skip_locked=True)
        if dataset is None or dataset.state != "active":
            continue
        statement = select(rechecks).where(
            history_scope_predicate(rechecks, scope),
            rechecks.c.generation == dataset.generation,
            *due,
        )
        if dataset.configuration.workflow_ids is not None:
            statement = statement.where(
                rechecks.c.workflow_id.in_(dataset.configuration.workflow_ids)
            )
        ordered = (
            statement.order_by(rechecks.c.next_attempt_at, rechecks.c.workflow_run_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = (await connection.execute(ordered)).mappings().one_or_none()
        if row is None:
            continue
        prior = decode_history_recheck(row)
        now = await history_database_time(connection)
        if prior.acquisition_count == MAX_HISTORY_RECHECK_ACQUISITIONS:
            recovered = await recover_recheck_exhaustion(connection, dataset, prior, now=now)
            if recovered == "applied":
                return "recovered"
            if recovered != "capacity_reached":
                continue
            capacity = True
            row = (
                (
                    await connection.execute(
                        ordered.where(
                            rechecks.c.acquisition_count < MAX_HISTORY_RECHECK_ACQUISITIONS
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                continue
            prior = decode_history_recheck(row)
            now = await history_database_time(connection)
        claim = acquire_history_recheck(
            dataset, prior, now=now, worker_id=worker_id, token=secrets.token_hex(32)
        )
        if claim is None:
            continue
        await write_history_recheck(connection, prior, claim.state, authority="acquire")
        return claim
    return "capacity_reached" if capacity else None
