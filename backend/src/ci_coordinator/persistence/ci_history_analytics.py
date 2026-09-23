from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.analytics_configuration import PurposeSettingsQuery
from ci_coordinator.ci_economics.archive_analytics_models import (
    MAX_ANALYTICS_ATTEMPTS,
    MAX_ANALYTICS_JOBS,
    AnalyticsQuery,
    AnalyticsSnapshot,
    AnalyticsUnavailable,
    CohortEvidence,
    DailyBucket,
    JobAggregate,
    PurposeMapping,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence._schema_analytics_purpose import analytics_purpose_settings
from ci_coordinator.persistence.analytics_purpose_store import (
    decode_purpose_settings,
    load_purpose_settings,
)
from ci_coordinator.persistence.ci_history_analytics_snapshot import analytics_snapshot_rows
from ci_coordinator.persistence.ci_history_control_codec import decode_history_dataset
from ci_coordinator.persistence.ci_history_state_store import load_history_dataset
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresPurposeUnitOfWork
from ci_coordinator.persistence.errors import PersistenceError


class TransactionalHistoryAnalyticsStore:
    def __init__(self, unit_of_work: Callable[[], PostgresPurposeUnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    async def read_analytics(
        self, query: AnalyticsQuery, mapping: PurposeMapping | None = None
    ) -> AnalyticsSnapshot | AnalyticsUnavailable:
        query = AnalyticsQuery.model_validate(query)
        mapping = None if mapping is None else PurposeMapping.model_validate(mapping)
        try:
            async with self._unit_of_work() as transaction:
                connection = transaction.history_connection
                await connection.execute(select(func.set_config("statement_timeout", "5000", True)))
                return await _read_snapshot(connection, query, mapping)
        except (SQLAlchemyError, PersistenceError, ValueError, TypeError, OverflowError) as error:
            raise CiEconomicsStoreUnavailable("archive analytics unavailable") from error


async def _read_snapshot(
    connection: AsyncConnection, query: AnalyticsQuery, mapping: PurposeMapping | None
) -> AnalyticsSnapshot | AnalyticsUnavailable:
    dataset = await load_history_dataset(connection, query.scope)
    if dataset is None or dataset.state not in {"active", "paused"}:
        return AnalyticsUnavailable(reason="dataset_unavailable")
    if dataset.generation != query.generation:
        return AnalyticsUnavailable(reason="generation_changed")
    settings_query = PurposeSettingsQuery(
        installation_id=query.installation_id,
        repository_id=query.repository_id,
        generation=query.generation,
    )
    settings = await load_purpose_settings(connection, settings_query)
    if mapping is not None and mapping != settings.mapping:
        return AnalyticsUnavailable(reason="snapshot_changed")
    mapping = settings.mapping
    if mapping is None and query.purpose not in {None, "unknown"}:
        return AnalyticsUnavailable(reason="purpose_mapping_unavailable")
    rows = (await connection.execute(analytics_snapshot_rows(query, mapping))).mappings().all()
    if not rows:
        return AnalyticsUnavailable(reason="dataset_unavailable")
    if len(rows) > 366:
        raise ValueError("daily result exceeds its budget")
    metadata = rows[0]
    dataset = decode_history_dataset(metadata)
    if dataset.state not in {"active", "paused"}:
        return AnalyticsUnavailable(reason="dataset_unavailable")
    if dataset.generation != query.generation:
        return AnalyticsUnavailable(reason="generation_changed")
    settings_row = (
        None
        if metadata["purpose_installation_id"] is None
        else {
            column.name: metadata[f"purpose_{column.name}"]
            for column in analytics_purpose_settings.c
        }
    )
    if decode_purpose_settings(settings_row, settings_query) != settings:
        return AnalyticsUnavailable(reason="snapshot_changed")
    observed_at = metadata["observed_at"]
    if type(observed_at) is not datetime:
        raise ValueError("analytics database time is unavailable")
    if query.created_until > observed_at:
        return AnalyticsUnavailable(reason="future_window")
    population = {
        name: metadata[f"population_{name}"]
        for name in (
            "attempts",
            "runs",
            "archived_jobs",
            "known_versions",
            "unknown_versions",
            "events",
        )
    }
    if population["attempts"] > MAX_ANALYTICS_ATTEMPTS:
        return AnalyticsUnavailable(reason="query_budget_exceeded")
    job_count = metadata["scoped_job_count"]
    if type(job_count) is not int:
        raise ValueError("analytics job count unavailable")
    if job_count > MAX_ANALYTICS_JOBS:
        return AnalyticsUnavailable(reason="query_budget_exceeded")
    if job_count != population["archived_jobs"]:
        raise ValueError("archive scalar job population contradicts attempt totals")
    changes = metadata["definition_changes"]
    if type(changes) is not int:
        raise ValueError("analytics definition changes unavailable")
    by_day = {row["day"]: row for row in rows if row["day"] is not None}
    buckets = tuple(
        _bucket(day, by_day.get(day))
        for day in (
            query.created_from + timedelta(days=index)
            for index in range((query.created_until - query.created_from).days)
        )
    )
    stable = population["attempts"] > 0 and population["unknown_versions"] == 0 and changes == 0
    return AnalyticsSnapshot(
        query=query,
        observed_at=observed_at,
        data_revision=dataset.data_revision,
        configuration_revision=dataset.configuration_revision,
        dataset_state=dataset.state,
        mapping=mapping,
        runs=population["runs"],
        attempts=population["attempts"],
        buckets=buckets,
        cohort=CohortEvidence(
            known_workflow_versions=population["known_versions"],
            unknown_workflow_attempts=population["unknown_versions"],
            event_count=population["events"],
            definition_stable=stable,
            observed_definition_changes=changes > 0,
            compatible=stable and query.workflow_id is not None and query.job_name is not None,
        ),
    )


def _bucket(day: datetime, row: Mapping[str, object] | RowMapping | None) -> DailyBucket:
    selected = (
        JobAggregate()
        if row is None
        else JobAggregate.model_validate({name: row[name] for name in JobAggregate.model_fields})
    )
    counts = {
        name: 0 if row is None else row[name]
        for name in (
            "runs",
            "attempts",
            "matching_attempts",
            "complete_attempts",
            "partial_attempts",
            "unavailable_attempts",
            "conflict_attempts",
            "known_missing_jobs",
            "unknown_population_attempts",
            "conflict_excluded_jobs",
        )
    }
    return DailyBucket.model_validate(
        {
            **counts,
            "day": day,
            "coverage": "unknown"
            if row is None
            else (
                "complete_retained"
                if counts["attempts"] == counts["complete_attempts"]
                else "partial"
            ),
            "selected": selected,
            "observed_runner_ms": selected.runner_ms if selected.duration_samples else None,
            "observed_queue_ms": selected.queue_ms if selected.queue_samples else None,
        }
    )
