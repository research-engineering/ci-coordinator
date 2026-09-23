from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import event, func, select
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store

from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import ci_workflow_attempt_collections as collections

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("reverse", [False, True])
def test_batch_correlates_mixed_outcomes_and_preserves_existing_provenance(
    runtime_postgres_database_url: str, reverse: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            existing = provider_source(10, now)
            fresh = provider_source(20, now)
            conflict = replace(existing, source_evidence_digest="c" * 64)
            old = provider_source(30, now - timedelta(days=8))
            future = provider_source(40, now + timedelta(days=1))
            assert await store(engine).register_provider_source(existing) == "registered"
            cases = [
                (fresh, "registered"),
                (existing, "replayed"),
                (conflict, "source_conflict"),
                (old, "outside_source_window"),
                (future, "outside_source_window"),
            ]
            if reverse:
                cases.reverse()
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                results = await transaction.ci_economics_collection.register_provider_sources(
                    existing.attempt.scope, tuple(source for source, _ in cases)
                )
                assert results == tuple(outcome for _, outcome in cases)
                await transaction.commit()
            async with engine.connect() as connection:
                rows = (await connection.execute(select(collections))).mappings().all()
            assert {row["subject_id"] for row in rows} == {existing.source_id, fresh.source_id}
            assert all(row["source_evidence_digest"] == "b" * 64 for row in rows)
            assert all(row["source_created_at"] == now for row in rows)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reverse", [False, True])
def test_same_page_duplicates_replay_and_conflicting_natural_identity_cannot_fork(
    runtime_postgres_database_url: str, reverse: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(1, await database_now(engine))
            alternate = replace(source, attempt=replace(source.attempt, head_sha="c" * 40))
            sources: tuple[ProviderRunCollectionSource, ...] = (source, source, alternate)
            expected = ("registered", "replayed", "source_conflict")
            if reverse:
                sources = tuple(reversed(sources))
                expected = ("source_conflict", "registered", "replayed")
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert (
                    await transaction.ci_economics_collection.register_provider_sources(
                        source.attempt.scope, sources
                    )
                    == expected
                )
                await transaction.commit()
            assert await store(engine).register_provider_source(source) == "replayed"
            async with engine.connect() as connection:
                assert await connection.scalar(select(collections.c.subject_id)) == source.source_id
                assert await connection.scalar(select(func.count()).select_from(collections)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [0, 1, 100])
def test_batch_query_count_is_constant_and_replay_does_not_recount_population(
    runtime_postgres_database_url: str, count: int
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            sources = tuple(provider_source(run, now) for run in range(1, count + 1))
            scope = RepositoryScope(101, 202)
            statements: list[str] = []

            def capture(
                _connection: object, _cursor: object, statement: str, *_args: object
            ) -> None:
                statements.append(statement)

            for replay in (False, True):
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    statements.clear()
                    event.listen(engine.sync_engine, "before_cursor_execute", capture)
                    try:
                        result = (
                            await transaction.ci_economics_collection.register_provider_sources(
                                scope, sources
                            )
                        )
                    finally:
                        event.remove(engine.sync_engine, "before_cursor_execute", capture)
                    assert result == (("replayed" if replay else "registered"),) * count
                    assert len(statements) == (0 if count == 0 else 3 if replay else 5)
                    await transaction.commit()
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(collections)) == count
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["scope", "list", "member", "oversized"])
def test_invalid_batch_rolls_back_prior_uncommitted_registration(
    runtime_postgres_database_url: str, invalid: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(1, await database_now(engine))
            scope = source.attempt.scope
            batches: dict[str, object] = {
                "scope": (
                    replace(
                        source, attempt=replace(source.attempt, scope=RepositoryScope(101, 203))
                    ),
                ),
                "list": [source],
                "member": (source, None),
                "oversized": (source,) * 101,
            }
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                repository = transaction.ci_economics_collection
                assert await repository.register_provider_source(source) == "registered"
                with pytest.raises(PersistenceInvariantViolation):
                    await repository.register_provider_sources(
                        scope, cast(tuple[ProviderRunCollectionSource, ...], batches[invalid])
                    )
                with pytest.raises(RuntimeError, match="not active"):
                    await transaction.commit()
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(collections)) == 0
            assert await store(engine).register_provider_source(source) == "registered"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_aborted_batch_leaves_no_rows_and_same_inputs_can_be_retried(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            sources = tuple(provider_source(run, now) for run in (1, 2, 3))
            scope = sources[0].attempt.scope
            with pytest.raises(RuntimeError, match="after registration"):
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    assert (
                        await transaction.ci_economics_collection.register_provider_sources(
                            scope, sources
                        )
                        == ("registered",) * 3
                    )
                    raise RuntimeError("after registration, before page commit")
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(collections)) == 0
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert (
                    await transaction.ci_economics_collection.register_provider_sources(
                        scope, sources
                    )
                    == ("registered",) * 3
                )
                await transaction.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
