"""Append-only persistence of history population-bound provenance."""

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics._observation_values import utc_time
from ci_coordinator.ci_economics.history_population_bound import HistoryPopulationBound
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_population_bound import (
    ci_history_population_bounds,
)
from ci_coordinator.persistence.ci_history_state_store import history_scope_predicate


async def insert_population_bound(
    connection: AsyncConnection, bound: HistoryPopulationBound
) -> None:
    if type(bound) is not HistoryPopulationBound:
        raise TypeError("history population bound persistence requires an exact bound")
    await connection.execute(
        insert(ci_history_population_bounds).values(
            installation_id=bound.scope.installation_id,
            repository_id=bound.scope.repository_id,
            generation=bound.generation,
            created_from=bound.created_from,
            source=bound.source,
            repository_created_at=bound.repository_created_at,
            configuration_revision=bound.configuration_revision,
            recorded_at=bound.recorded_at,
        )
    )


def decode_population_bound(row: Mapping[str, object] | RowMapping) -> HistoryPopulationBound:
    installation_id, repository_id, generation, revision = (
        row.get(name)
        for name in ("installation_id", "repository_id", "generation", "configuration_revision")
    )
    created_from, created_at, recorded_at = (
        row.get(name) for name in ("created_from", "repository_created_at", "recorded_at")
    )
    source = row.get("source")
    if (
        type(installation_id) is not int
        or type(repository_id) is not int
        or type(generation) is not int
        or type(revision) is not int
        or type(created_from) is not datetime
        or type(recorded_at) is not datetime
        or (created_at is not None and type(created_at) is not datetime)
    ):
        raise ValueError("history population bound row is malformed")
    if source == "explicit" or source == "repository_created_at":
        return HistoryPopulationBound(
            RepositoryScope(installation_id, repository_id),
            generation,
            created_from,
            source,
            created_at,
            revision,
            recorded_at,
        )
    raise ValueError("history population bound has an unknown source")


async def catalog_bound_is_recorded(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    generation: int,
    created_from: datetime,
    repository_created_at: datetime,
) -> bool:
    row = (
        (
            await connection.execute(
                select(ci_history_population_bounds).where(
                    history_scope_predicate(ci_history_population_bounds, scope),
                    ci_history_population_bounds.c.generation == generation,
                    ci_history_population_bounds.c.created_from == created_from,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False
    bound = decode_population_bound(row)
    return bound.source == "repository_created_at" and bound.repository_created_at == utc_time(
        repository_created_at
    )
