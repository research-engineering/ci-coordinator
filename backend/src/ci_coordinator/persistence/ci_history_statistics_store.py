from dataclasses import replace
from datetime import datetime
from typing import Literal

from sqlalchemy import Table, and_, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.ci_economics._observation_values import positive_id, utc_time
from ci_coordinator.ci_economics.archive_encoding import MAX_HISTORY_STATISTICS_BYTES
from ci_coordinator.ci_economics.archive_refinement import classify_archive_update
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryUsage
from ci_coordinator.ci_economics.model import MAX_JOBS_PER_ATTEMPT, AttemptIdentity
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts,
    ci_history_jobs,
)
from ci_coordinator.persistence.canonical_row import require_bytes
from ci_coordinator.persistence.ci_history_codec import (
    decode_archive_statistics,
    encode_archive_statistics,
)
from ci_coordinator.persistence.ci_history_state_store import (
    history_scope_predicate,
    write_history_dataset,
)

type StatisticsStoreOutcome = Literal[
    "recorded", "refined", "replayed", "incomparable", "conflict", "capacity_reached"
]


def history_attempt_predicate(
    table: Table, generation: int, attempt: AttemptIdentity
) -> ColumnElement[bool]:
    positive_id(generation, "history generation")
    if type(attempt) is not AttemptIdentity:
        raise TypeError("history storage requires exact attempt identity")
    return and_(
        history_scope_predicate(table, attempt.scope),
        table.c.generation == generation,
        table.c.workflow_run_id == attempt.workflow_run_id,
        table.c.run_attempt == attempt.run_attempt,
    )


async def store_history_statistics(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    statistics: ArchivedAttemptStatistics,
    *,
    now: datetime,
) -> tuple[HistoryDataset, StatisticsStoreOutcome]:
    statistics = ArchivedAttemptStatistics.model_validate(statistics)
    now = utc_time(now)
    identity = statistics.attempt.to_attempt()
    if (
        dataset.state != "active"
        or dataset.scope != identity.scope
        or now < dataset.configured_at
        or not dataset.configuration.selects(statistics.workflow_id)
    ):
        raise ValueError("history contribution requires its current active dataset")
    encoded = encode_archive_statistics(statistics, generation=dataset.generation)
    table = ci_history_attempts
    predicate = history_attempt_predicate(table, dataset.generation, identity)
    prior = (
        (await connection.execute(select(table).where(predicate).with_for_update()))
        .mappings()
        .one_or_none()
    )
    removed = HistoryUsage.empty()
    changed_jobs = encoded.jobs
    outcome: StatisticsStoreOutcome = "recorded"
    if prior is not None:
        jobs = await load_history_jobs(connection, dataset.generation, identity)
        previous_statistics = decode_archive_statistics(prior, jobs)
        if type(prior["has_conflict"]) is not bool:
            raise ValueError("stored history conflict flag is malformed")
        decision = (
            "conflict"
            if prior["has_conflict"]
            else classify_archive_update(previous_statistics, statistics)
        )
        if decision == "unchanged":
            return dataset, "replayed"
        if decision == "incomparable":
            return dataset, "incomparable"
        if decision == "conflict":
            if prior["has_conflict"]:
                return dataset, "conflict"
            await connection.execute(update(table).where(predicate).values(has_conflict=True))
            changed = replace(dataset, data_revision=dataset.data_revision + 1)
            await write_history_dataset(connection, dataset, changed)
            return changed, "conflict"
        removed = HistoryUsage(
            attempts=1,
            jobs=len(previous_statistics.jobs),
            gaps=0,
            canonicalBytes=prior["statistics_bytes"],
        )
        previous_jobs = {
            row["provider_job_id"]: require_bytes(row["job_canonical"], "archived job")
            for row in jobs
        }
        changed_jobs = tuple(
            row
            for row in encoded.jobs
            if previous_jobs.get(row["provider_job_id"]) != row["job_canonical"]
        )
        outcome = "refined"
    addition = HistoryUsage(
        attempts=1, jobs=len(statistics.jobs), gaps=0, canonicalBytes=encoded.canonical_bytes
    )
    usage = dataset.usage.release(removed).reserve(addition, dataset.configuration.quota)
    if usage is None:
        return dataset, "capacity_reached"
    if prior is None:
        await connection.execute(
            insert(table).values(
                **encoded.attempt,
                has_conflict=statistics.population == "conflict",
                first_imported_at=now,
                detail_state="not_imported",
                detail_first_imported_at=None,
                detail_policy_canonical=None,
                detail_policy_source=None,
                detail_policy_revision=None,
                detail_expires_at=None,
            )
        )
    else:
        await connection.execute(
            update(table)
            .where(predicate)
            .values(
                **{
                    name: encoded.attempt[name]
                    for name in (
                        "header_canonical",
                        "statistics_digest",
                        "job_count",
                        "statistics_bytes",
                    )
                }
            )
        )
    if changed_jobs:
        statement = pg_insert(ci_history_jobs)
        await connection.execute(
            statement.on_conflict_do_update(
                constraint="pk_ci_history_jobs",
                set_={
                    name: statement.excluded[name]
                    for name in (
                        "name",
                        "conclusion",
                        "created_at",
                        "started_at",
                        "completed_at",
                        "job_canonical",
                    )
                },
            ),
            list(changed_jobs),
        )
    changed = replace(dataset, usage=usage, data_revision=dataset.data_revision + 1)
    await write_history_dataset(connection, dataset, changed)
    return changed, outcome


async def load_history_jobs(
    connection: AsyncConnection, generation: int, attempt: AttemptIdentity
) -> tuple[RowMapping, ...]:
    statement = (
        select(ci_history_jobs)
        .where(history_attempt_predicate(ci_history_jobs, generation, attempt))
        .order_by(ci_history_jobs.c.provider_job_id)
        .limit(MAX_JOBS_PER_ATTEMPT + 1)
        .execution_options(yield_per=100)
    )
    rows: list[RowMapping] = []
    byte_count = 0
    async with connection.stream(statement) as result:
        async for row in result.mappings():
            byte_count += len(require_bytes(row["job_canonical"], "archived job"))
            if byte_count > MAX_HISTORY_STATISTICS_BYTES or len(rows) == MAX_JOBS_PER_ATTEMPT:
                raise ValueError("archived jobs exceed their aggregate read budget")
            rows.append(row)
    return tuple(rows)
