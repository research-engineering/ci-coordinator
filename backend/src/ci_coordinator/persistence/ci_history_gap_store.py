from dataclasses import replace
from datetime import datetime
from typing import Literal

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryUsage
from ci_coordinator.ci_economics.history_gap import HistoryGap, HistoryRecheckGap
from ci_coordinator.persistence._schema_ci_history_control import ci_history_gaps
from ci_coordinator.persistence.canonical_row import encode_canonical_object, require_bytes
from ci_coordinator.persistence.ci_history_state_store import (
    history_scope_predicate,
    write_history_dataset,
)

MAX_HISTORY_GAP_BYTES = 4096


async def store_history_gap(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    gap: HistoryGap | HistoryRecheckGap,
    *,
    now: datetime,
) -> tuple[HistoryDataset, Literal["recorded", "replayed", "capacity_reached"]]:
    if type(gap) is HistoryGap:
        gap = HistoryGap.model_validate(gap)
        source_time = gap.cursor.to_cursor().cycle_started_at
    elif type(gap) is HistoryRecheckGap:
        gap = HistoryRecheckGap.model_validate(gap)
        source_time = gap.run_created_at
        if not dataset.configuration.selects(gap.workflow_id):
            raise ValueError("recheck gap requires a selected workflow")
    else:
        raise TypeError("history gap requires an exact admitted observation")
    now = utc_time(now)
    if (
        dataset.state != "active"
        or dataset.scope != gap.scope
        or dataset.generation != gap.generation
        or dataset.configuration_revision != gap.configuration_revision
        or now < dataset.configured_at
        or now < source_time
    ):
        raise ValueError("history gap does not match its current dataset and scan epoch")
    raw = encode_canonical_object(
        gap.model_dump(mode="json"), maximum_bytes=MAX_HISTORY_GAP_BYTES, context="history gap"
    )
    table = ci_history_gaps
    prior = await connection.scalar(
        select(table.c.gap_canonical).where(
            history_scope_predicate(table, dataset.scope),
            table.c.generation == dataset.generation,
            table.c.gap_id == gap.gap_id,
        )
    )
    if prior is not None:
        if require_bytes(prior, "history gap") != raw:
            raise ValueError("history gap identity names contradictory canonical bytes")
        return dataset, "replayed"
    usage = dataset.usage.reserve(
        HistoryUsage(attempts=0, jobs=0, gaps=1, canonicalBytes=len(raw)),
        dataset.configuration.quota,
    )
    if usage is None:
        return dataset, "capacity_reached"
    await connection.execute(
        insert(table).values(
            installation_id=dataset.scope.installation_id,
            repository_id=dataset.scope.repository_id,
            generation=dataset.generation,
            gap_id=gap.gap_id,
            gap_canonical=raw,
            recorded_at=now,
        )
    )
    changed = replace(dataset, usage=usage, data_revision=dataset.data_revision + 1)
    await write_history_dataset(connection, dataset, changed)
    return changed, "recorded"
