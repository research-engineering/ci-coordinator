from sqlalchemy import Table, and_, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.history_administration import HistoryStatus
from ci_coordinator.ci_economics.history_rechecks import MAX_HISTORY_RECHECK_RUNS
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets,
    ci_history_defaults,
    ci_history_rechecks,
    ci_history_scans,
)
from ci_coordinator.persistence.ci_history_control_codec import (
    decode_history_dataset,
    decode_history_scan,
)
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_defaults
from ci_coordinator.persistence.ci_history_state_store import history_scope_predicate


async def history_status(connection: AsyncConnection, scope: RepositoryScope) -> HistoryStatus:
    discovery = ci_history_scans.alias("discovery")
    rechecks = (
        select(ci_history_rechecks.c.workflow_run_id)
        .where(
            history_scope_predicate(ci_history_rechecks, scope),
            ci_history_rechecks.c.generation == ci_history_datasets.c.generation,
        )
        .correlate(ci_history_datasets)
        .limit(MAX_HISTORY_RECHECK_RUNS + 1)
        .subquery()
    )
    pending_count = select(func.count()).select_from(rechecks).scalar_subquery()
    sources = (
        ("defaults", ci_history_defaults),
        ("dataset", ci_history_datasets),
        ("scan", ci_history_scans),
        ("discovery", discovery),
    )
    statement = (
        select(
            *(
                column.label(f"{prefix}_{column.name}")
                for prefix, table in sources
                for column in table.c
            ),
            pending_count.label("pending_rechecks"),
            func.statement_timestamp().label("observed_at"),
        )
        .select_from(
            ci_history_defaults.outerjoin(
                ci_history_datasets, history_scope_predicate(ci_history_datasets, scope)
            )
            .outerjoin(
                ci_history_scans,
                and_(
                    history_scope_predicate(ci_history_scans, scope),
                    ci_history_scans.c.lane == "backfill",
                ),
            )
            .outerjoin(
                discovery,
                and_(
                    discovery.c.installation_id == scope.installation_id,
                    discovery.c.repository_id == scope.repository_id,
                    discovery.c.lane == "discovery",
                ),
            )
        )
        .where(ci_history_defaults.c.singleton.is_(True))
    )
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise ValueError("history defaults are unavailable")
    return HistoryStatus(
        scope,
        decode_history_defaults(_columns(row, ci_history_defaults, "defaults")),
        None
        if row["dataset_installation_id"] is None
        else decode_history_dataset(_columns(row, ci_history_datasets, "dataset")),
        None
        if row["scan_installation_id"] is None
        else decode_history_scan(_columns(row, ci_history_scans, "scan")),
        row["pending_rechecks"],
        row["observed_at"],
        None
        if row["discovery_installation_id"] is None
        else decode_history_scan(_columns(row, ci_history_scans, "discovery")),
    )


def _columns(row: RowMapping, table: Table, prefix: str) -> dict[str, object]:
    return {column.name: row[f"{prefix}_{column.name}"] for column in table.c}
