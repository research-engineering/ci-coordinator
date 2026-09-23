from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from time import perf_counter_ns

import pytest
from sqlalchemy import Select, event, func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import AsyncAdaptedQueuePool
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store

from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.observation_scan import OBSERVATION_LEASE_SECONDS
from ci_coordinator.ci_economics.sources import MAX_PROVIDER_SOURCES_PER_REPOSITORY
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import ci_workflow_attempt_collections as collections

pytestmark = pytest.mark.persistence


def test_full_population_batch_admits_deterministic_slots_and_preserves_age_precedence(
    runtime_postgres_database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            policy = load_bundled_ci_economics_profile().collection_policy
            limit = MAX_PROVIDER_SOURCES_PER_REPOSITORY
            old = provider_source(1, now - timedelta(days=8))
            retained = (old, *(provider_source(run, now) for run in range(2, limit - 1)))
            records = [
                encode_collection_record(
                    initial_collection_state(
                        source.source_id, source.run_created_at, source.run_created_at, policy
                    ),
                    source,
                )
                for source in retained
            ]
            foreign_sources = tuple(
                replace(
                    source, attempt=replace(source.attempt, scope=RepositoryScope(101, repository))
                )
                for repository in (203, 204)
                for source in retained
            )
            async with engine.begin() as connection:
                await connection.execute(insert(collections), records)
                await connection.execute(
                    insert(collections),
                    [
                        encode_collection_record(
                            initial_collection_state(
                                source.source_id,
                                source.run_created_at,
                                source.run_created_at,
                                policy,
                            ),
                            source,
                        )
                        for source in foreign_sources
                    ],
                )
            assert len(records) == limit - 2
            batch = (
                provider_source(limit + 1, now),
                old,
                retained[1],
                provider_source(limit - 1, now),
                provider_source(limit, now),
            )
            statements: list[str] = []
            selections: list[str] = []

            def capture_selection(_connection: object, statement: object, *_args: object) -> None:
                if isinstance(statement, Select) and statement.get_final_froms():
                    selections.append(
                        str(
                            statement.compile(
                                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                            )
                        )
                    )

            def capture(
                _connection: object, _cursor: object, statement: str, *_args: object
            ) -> None:
                statements.append(statement)

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                event.listen(engine.sync_engine, "before_execute", capture_selection)
                try:
                    await transaction.ci_economics_collection.register_provider_sources(
                        old.attempt.scope, batch
                    )
                finally:
                    event.remove(engine.sync_engine, "before_execute", capture_selection)
                await transaction.rollback()
            assert len(selections) == 2
            plans: list[object] = []
            async with engine.connect() as connection:
                for statement in selections:
                    plan = await connection.scalar(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement)
                    )
                    assert isinstance(plan, list) and len(plan) == 1
                    plans.append(plan[0])
                assert await connection.scalar(text(selections[-1])) == limit - 2
            async with (
                asyncio.timeout(OBSERVATION_LEASE_SECONDS),
                PostgresCiEconomicsUnitOfWork(engine) as transaction,
            ):
                event.listen(engine.sync_engine, "before_cursor_execute", capture)
                started = perf_counter_ns()
                try:
                    result = await transaction.ci_economics_collection.register_provider_sources(
                        old.attempt.scope, batch
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture)
                elapsed = perf_counter_ns() - started
                assert result == (
                    "capacity_reached",
                    "outside_source_window",
                    "replayed",
                    "registered",
                    "registered",
                )
                assert len(statements) == 5
                await transaction.commit()
            async with engine.connect() as connection:
                assert await connection.scalar(
                    select(func.count()).select_from(collections)
                ) == limit + len(foreign_sources)
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(collections)
                        .where(collections.c.subject_id == batch[0].source_id)
                    )
                    == 0
                )
            assert await store(engine).register_provider_source(old) == "outside_source_window"
            assert await store(engine).register_provider_source(batch[3]) == "replayed"
            assert await store(engine).register_provider_source(batch[0]) == "capacity_reached"
            assert isinstance(engine.pool, AsyncAdaptedQueuePool) and engine.pool.checkedout() == 0
            with capsys.disabled():
                print(
                    json.dumps(
                        {
                            "observation": "provider-source-batch-capacity/v1",
                            "retainedSources": limit,
                            "batchSize": len(batch),
                            "neighboringSources": len(foreign_sources),
                            "populatedSelectionPlans": plans,
                            "registrationStatements": len(statements),
                            "registrationElapsedNanoseconds": elapsed,
                            "nonClaims": [
                                "Production capacity and child cleanup throughput remain unproven."
                            ],
                        },
                        sort_keys=True,
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["sql", "cancelled"])
def test_post_insert_failure_poisoned_transaction_cannot_publish_partial_batch(
    runtime_postgres_database_url: str, failure: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            sources = tuple(provider_source(run, now) for run in (1, 2))
            reached = False

            def fail_after_insert(
                _connection: object, _cursor: object, statement: str, *_args: object
            ) -> None:
                nonlocal reached
                if statement.startswith(
                    "INSERT INTO ci_coordinator.ci_workflow_attempt_collections"
                ):
                    reached = True
                    if failure == "cancelled":
                        raise asyncio.CancelledError("injected after batch insert")
                    raise SQLAlchemyError("injected after batch insert")

            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                event.listen(engine.sync_engine, "after_cursor_execute", fail_after_insert)
                try:
                    expected = (
                        asyncio.CancelledError
                        if failure == "cancelled"
                        else PersistenceInvariantViolation
                    )
                    with pytest.raises(expected):
                        await transaction.ci_economics_collection.register_provider_sources(
                            sources[0].attempt.scope, sources
                        )
                finally:
                    event.remove(engine.sync_engine, "after_cursor_execute", fail_after_insert)
                assert reached
                with pytest.raises(RuntimeError, match="not active"):
                    await transaction.commit()
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(collections)) == 0
            assert await store(engine).register_provider_source(sources[0]) == "registered"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
