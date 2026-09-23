import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_job, archived_statistics
from sqlalchemy import func, literal, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.selectable import Select

from ci_coordinator.ci_economics.analytics_configuration import (
    ConfigurePurposeSettings,
    PurposeSettingsQuery,
    PurposeSettingsSaved,
    PurposeSettingsSnapshot,
)
from ci_coordinator.ci_economics.archive_analytics import summarize_archive
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsQuery,
    AnalyticsSnapshot,
    AnalyticsUnavailable,
    PurposeEntry,
    PurposeMapping,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.history_commands import HistoryConfigured
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence.analytics_purpose_store import (
    TransactionalPurposeSettingsStore,
    load_purpose_settings,
)
from ci_coordinator.persistence.ci_history_analytics import TransactionalHistoryAnalyticsStore
from ci_coordinator.persistence.ci_history_control_codec import encode_history_dataset
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
)
from ci_coordinator.persistence.ci_history_statistics_store import store_history_statistics
from ci_coordinator.persistence.ci_history_unit_of_work import (
    PostgresHistoryUnitOfWork,
    PostgresPurposeUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_history_datasets

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence


def _query(**changes: object) -> AnalyticsQuery:
    return AnalyticsQuery.model_validate(
        {
            "installation_id": 101,
            "repository_id": 202,
            "generation": 1,
            "created_from": ARCHIVE_TIME,
            "created_until": ARCHIVE_TIME + timedelta(days=1),
            "workflow_id": 404,
            **changes,
        }
    )


async def _seed(engine: AsyncEngine, records: tuple[ArchivedAttemptStatistics, ...]) -> None:
    command = history_command()
    if records:
        command = type(command).model_validate(
            command.model_copy(
                update={
                    "installation_id": records[0].attempt.installation_id,
                    "repository_id": records[0].attempt.repository_id,
                }
            )
        )
    assert isinstance(await history_store(engine).configure_history(command), HistoryConfigured)
    async with PostgresHistoryUnitOfWork(engine) as transaction:
        connection = transaction.history_connection
        dataset = await load_history_dataset(connection, command.scope)
        assert dataset is not None
        now = await history_database_time(connection)
        for record in records:
            dataset, outcome = await store_history_statistics(connection, dataset, record, now=now)
            assert outcome in {"recorded", "conflict"}
        await transaction.commit()


def _record(
    run: int,
    *,
    attempt: int = 1,
    workflow: int = 404,
    repository: int = 202,
    jobs: tuple[object, ...] | None = None,
) -> ArchivedAttemptStatistics:
    source = archived_statistics()
    return ArchivedAttemptStatistics.model_validate(
        source.model_copy(
            update={
                "attempt": source.attempt.model_copy(
                    update={
                        "workflow_run_id": run,
                        "run_attempt": attempt,
                        "repository_id": repository,
                    }
                ),
                "workflow_id": workflow,
                **(
                    {}
                    if jobs is None
                    else {
                        "jobs": jobs,
                        "provider_job_total": len(jobs),
                    }
                ),
            }
        )
    )


def test_sql_aggregates_exact_scope_workflow_reruns_and_literal_job_names(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        try:
            await _seed(
                engine,
                (
                    _record(
                        1,
                        jobs=(
                            archived_job(1),
                            archived_job(2).model_copy(update={"name": "Build"}),
                        ),
                    ),
                    _record(1, attempt=2),
                    _record(2, workflow=405),
                ),
            )
            await _seed(engine, (_record(1, repository=203),))
            selected = await store.read_analytics(_query(job_name="Lint"), None)
            assert isinstance(selected, AnalyticsSnapshot)
            report = summarize_archive(selected)
            assert report.runs == 1 and report.attempts == 2
            assert report.buckets[0].selected.jobs == 2
            assert report.buckets[0].observed_runner_ms == 120000
            assert report.buckets[0].observed_queue_ms == 120000
            assert report.cohort.unknown_workflow_attempts == 2
            case_sensitive = await store.read_analytics(_query(job_name="lint"), None)
            assert isinstance(case_sensitive, AnalyticsSnapshot)
            assert case_sensitive.buckets[0].selected.jobs == 0
            assert case_sensitive.buckets[0].observed_runner_ms is None
            assert await store.read_analytics(_query(generation=2), None) == AnalyticsUnavailable(
                reason="generation_changed"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_native_mixed_purpose_is_counted_once_and_arbitrary_lint_names_stay_unknown(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        command = ConfigurePurposeSettings(
            installation_id=101,
            repository_id=202,
            generation=1,
            expected_revision=0,
            operation_id="mixed-purpose-settings",
            actor="operator-1",
            entries=(PurposeEntry(workflow_id=404, job_name="Mixed", purposes=("lint", "build")),),
        )
        try:
            await _seed(
                engine,
                (
                    _record(
                        1,
                        jobs=(
                            archived_job(1).model_copy(update={"name": "Mixed"}),
                            archived_job(2),
                        ),
                    ),
                ),
            )
            configured = await TransactionalPurposeSettingsStore(
                lambda: PostgresPurposeUnitOfWork(engine)
            ).configure_settings(command)
            assert isinstance(configured, PurposeSettingsSaved)
            mapping = configured.snapshot.mapping
            assert mapping is not None
            lint = await store.read_analytics(_query(purpose="lint"), None)
            assert isinstance(lint, AnalyticsSnapshot)
            assert lint.buckets[0].selected.jobs == lint.buckets[0].selected.mixed_jobs == 1
            assert lint.buckets[0].observed_runner_ms == 60000
            unknown = await store.read_analytics(_query(purpose="unknown"), None)
            assert isinstance(unknown, AnalyticsSnapshot)
            assert (
                unknown.buckets[0].selected.jobs
                == unknown.buckets[0].selected.unknown_purpose_jobs
                == 1
            )
            assert summarize_archive(lint).mapping_digest == mapping.digest
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_native_null_reversed_and_partial_timestamps_preserve_independent_sample_counts(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        jobs = (
            archived_job(1),
            archived_job(2).model_copy(update={"created_at": None, "conclusion": "failure"}),
            archived_job(3).model_copy(update={"completed_at": None, "conclusion": "cancelled"}),
            archived_job(4).model_copy(update={"started_at": ARCHIVE_TIME - timedelta(seconds=1)}),
            archived_job(5).model_copy(update={"started_at": None}),
        )
        try:
            await _seed(engine, (_record(1, jobs=jobs),))
            snapshot = await store.read_analytics(_query(), None)
            assert isinstance(snapshot, AnalyticsSnapshot)
            selected = snapshot.buckets[0].selected
            assert selected.jobs == 5 and selected.failures == selected.cancellations == 1
            assert selected.duration_samples == selected.queue_samples == 2
            assert selected.missing_duration == selected.missing_queue == 2
            assert selected.inconsistent_timings == 1
            assert selected.runner_ms == selected.queue_ms == 120000
            assert summarize_archive(snapshot).forecast.reason == "incomplete_daily_coverage"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_conflict_and_partial_population_are_not_complete_analytic_evidence(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        original = _record(1)
        conflicting = _record(
            1, jobs=(archived_job().model_copy(update={"conclusion": "failure"}),)
        )
        partial = _record(2).model_copy(update={"population": "partial", "provider_job_total": 3})
        try:
            await _seed(engine, (original, conflicting, partial))
            snapshot = await store.read_analytics(_query(), None)
            assert isinstance(snapshot, AnalyticsSnapshot)
            bucket = snapshot.buckets[0]
            assert bucket.coverage == "partial" and bucket.conflict_attempts == 1
            assert bucket.partial_attempts == 1 and bucket.known_missing_jobs == 2
            assert bucket.conflict_excluded_jobs == 1 and bucket.selected.jobs == 1
            assert bucket.observed_runner_ms == 60000
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("limit_name", ["MAX_ANALYTICS_ATTEMPTS", "MAX_ANALYTICS_JOBS"])
def test_sql_limit_overflow_does_not_execute_the_daily_projection(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
) -> None:
    import ci_coordinator.persistence.ci_history_analytics as adapter
    import ci_coordinator.persistence.ci_history_analytics_queries as queries
    import ci_coordinator.persistence.ci_history_analytics_snapshot as snapshot

    def guarded_daily(
        query: AnalyticsQuery, mapping: PurposeMapping | None
    ) -> Select[tuple[object, ...]]:
        execution_time_zero = func.random(0, 0)
        return queries.analytics_daily(query, mapping).add_columns(
            (literal(1) / execution_time_zero).label("must_not_execute")
        )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        try:
            await _seed(engine, (_record(1), _record(2)))
            with monkeypatch.context() as patch:
                for module in (adapter, queries, snapshot):
                    patch.setattr(module, limit_name, 2)
                boundary = await store.read_analytics(_query())
                assert isinstance(boundary, AnalyticsSnapshot)
                assert boundary.attempts == boundary.buckets[0].selected.jobs == 2
                for module in (adapter, queries, snapshot):
                    patch.setattr(module, limit_name, 1)
                # noinspection PyUnresolvedReferences
                patch.setattr(snapshot, "analytics_daily", guarded_daily)
                assert await store.read_analytics(_query(), None) == AnalyticsUnavailable(
                    reason="query_budget_exceeded"
                )
                patch.setattr(snapshot, limit_name, 2)
                with pytest.raises(CiEconomicsStoreUnavailable) as failure:
                    await store.read_analytics(_query())
                assert isinstance(failure.value.__cause__, DBAPIError)
                assert getattr(failure.value.__cause__.orig, "sqlstate", None) == "22012"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("fence", ["generation", "state"])
def test_statement_rejects_generation_or_state_changed_after_preparation(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    fence: str,
) -> None:
    import ci_coordinator.persistence.ci_history_analytics as adapter

    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)

        async def interleave(
            connection: AsyncConnection, query: PurposeSettingsQuery
        ) -> PurposeSettingsSnapshot:
            settings = await load_purpose_settings(connection, query)
            async with admin.begin() as writer:
                current = await load_history_dataset(writer, query.scope)
                assert current is not None
                successor = (
                    replace(current, generation=current.generation + 1)
                    if fence == "generation"
                    else replace(
                        current,
                        state="erasing",
                        configuration=current.configuration.model_copy(update={"enabled": False}),
                    )
                )
                await writer.execute(
                    update(ci_history_datasets).values(**encode_history_dataset(successor))
                )
            return settings

        try:
            await _seed(runtime, (_record(1),))
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(adapter, "load_purpose_settings", interleave)
            result = await TransactionalHistoryAnalyticsStore(
                lambda: PostgresPurposeUnitOfWork(runtime)
            ).read_analytics(_query())
            assert result == AnalyticsUnavailable(
                reason="generation_changed" if fence == "generation" else "dataset_unavailable"
            )
            async with admin.connect() as connection:
                current = await load_history_dataset(connection, _query().scope)
                assert current is not None
                assert current.generation == (2 if fence == "generation" else 1)
                assert current.state == ("active" if fence == "generation" else "erasing")
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_append_between_preparation_and_read_preserves_a_coherent_current_snapshot(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ci_coordinator.persistence.ci_history_analytics as adapter

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))

        async def interleaved_read(
            connection: AsyncConnection,
            query: PurposeSettingsQuery,
        ) -> PurposeSettingsSnapshot:
            settings = await load_purpose_settings(connection, query)
            async with PostgresHistoryUnitOfWork(engine) as writer:
                current = await load_history_dataset(writer.history_connection, query.scope)
                assert current is not None
                now = await history_database_time(writer.history_connection)
                _, outcome = await store_history_statistics(
                    writer.history_connection, current, _record(2), now=now
                )
                assert outcome == "recorded"
                await writer.commit()
            return settings

        try:
            await _seed(engine, (_record(1),))
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(adapter, "load_purpose_settings", interleaved_read)
            result = await store.read_analytics(_query(), None)
            assert isinstance(result, AnalyticsSnapshot)
            assert result.attempts == result.runs == result.buckets[0].selected.jobs == 2
            async with engine.connect() as connection:
                current = await load_history_dataset(connection, _query().scope)
                assert current is not None
                assert result.data_revision == current.data_revision
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_commit_after_statement_does_not_mix_snapshot_metadata_and_aggregates(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.sql import Executable
    from sqlalchemy.sql.selectable import Select

    original_execute = AsyncConnection.execute

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        statements = 0

        async def execute_then_append(
            connection: AsyncConnection, statement: Executable, *args: Any, **kwargs: Any
        ) -> CursorResult[Any]:
            nonlocal statements
            result = await original_execute(connection, statement, *args, **kwargs)
            if isinstance(statement, Select) and "scoped_job_count" in statement.selected_columns:
                statements += 1
                async with PostgresHistoryUnitOfWork(engine) as writer:
                    current = await load_history_dataset(writer.history_connection, _query().scope)
                    assert current is not None
                    now = await history_database_time(writer.history_connection)
                    _, outcome = await store_history_statistics(
                        writer.history_connection, current, _record(2), now=now
                    )
                    assert outcome == "recorded"
                    await writer.commit()
            return result

        try:
            await _seed(engine, (_record(1),))
            monkeypatch.setattr(AsyncConnection, "execute", execute_then_append)
            result = await store.read_analytics(_query())
            assert isinstance(result, AnalyticsSnapshot)
            assert statements == 1
            assert result.attempts == result.runs == result.buckets[0].selected.jobs == 1
            async with engine.connect() as connection:
                current = await load_history_dataset(connection, _query().scope)
                assert current is not None and current.usage.attempts == 2
                assert current.data_revision == result.data_revision + 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
