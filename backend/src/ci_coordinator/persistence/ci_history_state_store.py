from datetime import datetime

from sqlalchemy import Table, and_, func, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_scan import HistoryScanLane, HistoryScanState
from ci_coordinator.ci_economics.observation_scan import ObservationLease
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets,
    ci_history_scans,
)
from ci_coordinator.persistence.ci_history_control_codec import (
    decode_history_dataset,
    decode_history_scan,
    encode_history_dataset,
    encode_history_scan,
)


class HistoryWriteConflict(RuntimeError):
    pass


def history_scope_predicate(table: Table, scope: RepositoryScope) -> ColumnElement[bool]:
    if type(scope) is not RepositoryScope:
        raise TypeError("history storage requires an exact scope")
    return and_(
        table.c.installation_id == scope.installation_id,
        table.c.repository_id == scope.repository_id,
    )


async def lock_history_scope(
    connection: AsyncConnection, scope: RepositoryScope, *, try_only: bool = False
) -> bool:
    if type(scope) is not RepositoryScope or type(try_only) is not bool:
        raise TypeError("history lock requires exact scope and mode")
    statement = (
        "SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"
        if try_only
        else "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
    )
    result = await connection.scalar(
        text(statement),
        {
            "key": f"ci-history-scope/v1:{scope.installation_id}:{scope.repository_id}",
        },
    )
    return result is True if try_only else True


async def history_database_time(connection: AsyncConnection) -> datetime:
    value = await connection.scalar(select(func.clock_timestamp()))
    if type(value) is not datetime:
        raise ValueError("history database time unavailable")
    return utc_time(value)


def history_cas_time() -> ScalarSelect[datetime]:
    instant = (
        select(func.clock_timestamp().label("now"))
        .cte("history_cas_time")
        .prefix_with("MATERIALIZED")
    )
    return select(instant.c.now).scalar_subquery()


async def load_history_dataset(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    locked: bool = False,
    skip_locked: bool = False,
) -> HistoryDataset | None:
    row = await _load_scope(
        connection, ci_history_datasets, scope, locked=locked, skip_locked=skip_locked
    )
    return None if row is None else decode_history_dataset(row)


async def load_history_scan(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    lane: HistoryScanLane = "backfill",
    locked: bool = False,
    skip_locked: bool = False,
) -> HistoryScanState | None:
    if lane not in {"backfill", "discovery"}:
        raise ValueError("history scan lookup requires an admitted lane")
    row = await _load_scope(
        connection,
        ci_history_scans,
        scope,
        locked=locked,
        skip_locked=skip_locked,
        predicate=ci_history_scans.c.lane == lane,
    )
    return None if row is None else decode_history_scan(row)


async def write_history_dataset(
    connection: AsyncConnection, prior: HistoryDataset, successor: HistoryDataset
) -> None:
    if successor.scope != prior.scope:
        raise ValueError("dataset transition cannot substitute repository scope")
    table = ci_history_datasets
    result = await connection.execute(
        update(table)
        .where(
            history_scope_predicate(table, prior.scope),
            table.c.generation == prior.generation,
            table.c.configuration_revision == prior.configuration_revision,
            table.c.data_revision == prior.data_revision,
        )
        .values(**_mutable_scope_columns(encode_history_dataset(successor)))
    )
    if result.rowcount != 1:
        raise HistoryWriteConflict("history dataset CAS lost")


async def write_history_scan(
    connection: AsyncConnection,
    prior: HistoryScanState,
    successor: HistoryScanState,
    *,
    live_lease: ObservationLease | None = None,
) -> None:
    if (successor.scope, successor.lane) != (
        prior.scope,
        prior.lane,
    ) or successor.revision != prior.revision + 1:
        raise ValueError("scan transition must advance the exact prior revision")
    table = ci_history_scans
    predicates = [
        history_scope_predicate(table, prior.scope),
        table.c.lane == prior.lane,
        table.c.generation == prior.generation,
        table.c.configuration_revision == prior.configuration_revision,
        table.c.revision == prior.revision,
        table.c.state_canonical == encode_history_scan(prior)["state_canonical"],
    ]
    if live_lease is not None:
        if prior.lease != live_lease:
            raise ValueError("terminal scan CAS requires its exact prior lease")
        cas_time = history_cas_time()
        predicates.extend(
            [
                table.c.lease_worker_id == live_lease.worker_id,
                table.c.lease_token == live_lease.token,
                table.c.lease_acquired_at == live_lease.acquired_at,
                table.c.lease_expires_at == live_lease.expires_at,
                table.c.lease_acquired_at <= cas_time,
                table.c.lease_expires_at > cas_time,
            ]
        )
    elif successor.lease is not None:
        if (prior.generation, prior.configuration_revision) != (
            successor.generation,
            successor.configuration_revision,
        ):
            raise ValueError("history acquisition cannot change dataset authority")
        cas_time = history_cas_time()
        predicates.extend(
            [
                cas_time >= successor.lease.acquired_at,
                cas_time < successor.lease.expires_at,
            ]
        )
        if prior.lease is not None:
            predicates.append(cas_time >= prior.lease.expires_at)
    result = await connection.execute(
        update(table)
        .where(*predicates)
        .values(**_mutable_scope_columns(encode_history_scan(successor)))
    )
    if result.rowcount != 1:
        raise HistoryWriteConflict("history scan CAS lost or lease expired")


def _mutable_scope_columns(columns: dict[str, object]) -> dict[str, object]:
    return {
        name: value
        for name, value in columns.items()
        if name not in {"installation_id", "repository_id", "lane"}
    }


async def _load_scope(
    connection: AsyncConnection,
    table: Table,
    scope: RepositoryScope,
    *,
    locked: bool,
    skip_locked: bool,
    predicate: ColumnElement[bool] | None = None,
) -> RowMapping | None:
    if type(locked) is not bool or type(skip_locked) is not bool or (skip_locked and not locked):
        raise TypeError("history row lock mode must be exact boolean")
    statement = select(table).where(history_scope_predicate(table, scope))
    if predicate is not None:
        statement = statement.where(predicate)
    if locked:
        statement = statement.with_for_update(skip_locked=skip_locked)
    return (await connection.execute(statement)).mappings().one_or_none()
