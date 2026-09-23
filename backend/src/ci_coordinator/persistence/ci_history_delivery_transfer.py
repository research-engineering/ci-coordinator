from datetime import datetime
from typing import Final

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.ci_economics.history_configuration import MAX_HISTORY_DATASETS, HistoryDataset
from ci_coordinator.ci_economics.history_ports import HistoryDeliveryTransfer
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_control import ci_history_datasets
from ci_coordinator.persistence._schema_ci_history_delivery import (
    ci_history_delivery_inbox as inbox,
)
from ci_coordinator.persistence.ci_history_delivery_codec import (
    decode_history_delivery,
    history_delivery_fields,
)
from ci_coordinator.persistence.ci_history_recheck_rows import admit_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    HistoryWriteConflict,
    history_cas_time,
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
)
from ci_coordinator.persistence.schema import ci_workflow_observations as sources
from ci_coordinator.persistence.workflow_observation_lock import try_lock_workflow_observations

MAX_HISTORY_DELIVERY_BATCH: Final = 100


async def list_history_delivery_scopes(connection: AsyncConnection) -> tuple[RepositoryScope, ...]:
    rows = await connection.execute(
        select(ci_history_datasets.c.installation_id, ci_history_datasets.c.repository_id)
        .where(ci_history_datasets.c.state == "active")
        .order_by(ci_history_datasets.c.installation_id, ci_history_datasets.c.repository_id)
        .limit(MAX_HISTORY_DATASETS + 1)
    )
    scopes = tuple(RepositoryScope(row.installation_id, row.repository_id) for row in rows)
    if len(scopes) > MAX_HISTORY_DATASETS:
        raise ValueError("history dataset population exceeds its admitted bound")
    return scopes


async def transfer_history_deliveries(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    limit: int = MAX_HISTORY_DELIVERY_BATCH,
) -> HistoryDeliveryTransfer:
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_DELIVERY_BATCH:
        raise ValueError("history delivery batch limit is outside its admitted bound")
    if not await lock_history_scope(connection, scope, try_only=True):
        return HistoryDeliveryTransfer("busy", 0, 0)
    dataset = await load_history_dataset(connection, scope, locked=True, skip_locked=True)
    if dataset is None or dataset.state != "active":
        return HistoryDeliveryTransfer("inactive", 0, 0)
    expired_sample = await _expired_pending_sample(connection, dataset, limit)
    pending = and_(
        _pending_source(dataset), inbox.c.source_retain_until > func.statement_timestamp()
    )
    pending_rows = (
        await connection.execute(
            select(inbox.c.delivery_id, inbox.c.source_recorded_at, func.statement_timestamp())
            .where(pending)
            .order_by(inbox.c.delivered_generation, inbox.c.delivery_id)
            .limit(limit)
        )
    ).all()
    candidates = tuple(row[0] for row in pending_rows)
    if not candidates:
        return HistoryDeliveryTransfer("empty", 0, 0, expired_pending_sample=expired_sample)
    pending_age = max((row[2] - row[1]).total_seconds() for row in pending_rows)
    guarded = await try_lock_workflow_observations(connection, candidates)
    if not guarded:
        return HistoryDeliveryTransfer("busy", len(candidates), 0, pending_age, expired_sample)
    rows = (
        (
            await connection.execute(
                select(sources, *(value.label(f"inbox_{value.name}") for value in inbox.c))
                .select_from(inbox.join(sources, inbox.c.delivery_id == sources.c.delivery_id))
                .where(pending, inbox.c.delivery_id.in_(guarded))
                .order_by(inbox.c.delivered_generation, inbox.c.delivery_id)
                .with_for_update(of=inbox, skip_locked=True)
            )
        )
        .mappings()
        .all()
    )
    transferred = 0
    for row in rows:
        source = decode_history_delivery({column.name: row[column.name] for column in sources.c})
        expected = history_delivery_fields(source)
        if any(row[f"inbox_{name}"] != value for name, value in expected.items()):
            raise ValueError("history inbox differs from its retained source")
        if source.scope != dataset.scope or source.hint is None:
            raise ValueError("history inbox does not bind a supported scoped source")
        now = await history_database_time(connection)
        if not source.recorded_at <= now < source.retain_until:
            continue
        outcome = await admit_history_recheck(connection, dataset, source.hint, now=now)
        if outcome == "capacity_reached":
            return HistoryDeliveryTransfer(
                "capacity_reached", len(candidates), transferred, pending_age, expired_sample
            )
        if outcome not in {"admitted", "replayed"}:
            continue
        await _record_delivery_receipt(connection, dataset, row, source.hint.run_created_at)
        transferred += 1
    return HistoryDeliveryTransfer(
        "applied" if transferred else "deferred",
        len(candidates),
        transferred,
        pending_age,
        expired_sample,
    )


async def _expired_pending_sample(
    connection: AsyncConnection, dataset: HistoryDataset, limit: int
) -> int:
    sample = (
        await connection.scalars(
            select(inbox.c.source_retain_until <= func.statement_timestamp())
            .where(_pending_source(dataset))
            .order_by(inbox.c.delivered_generation, inbox.c.delivery_id)
            .limit(limit)
        )
    ).all()
    if any(type(expired) is not bool for expired in sample):
        raise ValueError("history expiry sample requires exact database predicates")
    return sum(expired is True for expired in sample)


def _pending_source(dataset: HistoryDataset) -> ColumnElement[bool]:
    now = func.statement_timestamp()
    predicate = and_(
        history_scope_predicate(inbox, dataset.scope),
        inbox.c.delivered_generation < dataset.generation,
        inbox.c.source_recorded_at <= now,
        inbox.c.run_created_at <= now,
    )
    if dataset.configuration.workflow_ids is not None:
        predicate = and_(predicate, inbox.c.workflow_id.in_(dataset.configuration.workflow_ids))
    return predicate


async def _record_delivery_receipt(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    prior: RowMapping,
    run_created_at: datetime,
) -> None:
    now = history_cas_time()
    prior_time = prior["inbox_delivered_at"]
    changed = await connection.execute(
        update(inbox)
        .where(
            history_scope_predicate(inbox, dataset.scope),
            inbox.c.delivery_id == prior["inbox_delivery_id"],
            inbox.c.source_fingerprint == prior["inbox_source_fingerprint"],
            inbox.c.delivered_generation == prior["inbox_delivered_generation"],
            inbox.c.delivered_generation < dataset.generation,
            inbox.c.delivered_at.is_(None)
            if prior_time is None
            else inbox.c.delivered_at == prior_time,
            inbox.c.source_recorded_at <= now,
            now >= max(dataset.configured_at, run_created_at),
            inbox.c.source_retain_until > now,
        )
        .values(delivered_generation=dataset.generation, delivered_at=now)
    )
    if changed.rowcount != 1:
        raise HistoryWriteConflict("history delivery lost its exact source authority")
