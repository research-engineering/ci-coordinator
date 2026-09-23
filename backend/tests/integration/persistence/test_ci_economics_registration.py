from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import DBAPIError
from tests.integration.persistence._ci_economics_support import database_now, store
from tests.integration.persistence._temporal_lock_support import wait_for_blocked_operation

from ci_coordinator.ci_economics import (
    AttemptIdentity,
    initial_collection_state,
    load_bundled_ci_economics_profile,
)
from ci_coordinator.ci_economics.sources import (
    MAX_PROVIDER_SOURCES_PER_REPOSITORY,
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_workflow_attempt_collections

pytestmark = pytest.mark.persistence


def _source(run: int, now: datetime) -> ProviderRunCollectionSource:
    return ProviderRunCollectionSource(
        AttemptIdentity(RepositoryScope(101, 202), run, 1, "a" * 40),
        now,
        "2026-03-10",
        "b" * 64,
    )


@pytest.mark.parametrize("offset_days", (-8, 1))
def test_registration_rejects_outside_source_window_without_state(
    runtime_postgres_database_url: str, offset_days: int
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            source = _source(1, now + timedelta(days=offset_days))
            assert await store(engine).register_provider_source(source) == "outside_source_window"
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_collections)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", "unknown"),
        ("source_kind", "reconciliation"),
        ("legacy_subject_id", "c" * 64),
        *(
            (field, None)
            for field in (
                "installation_id",
                "repository_id",
                "workflow_run_id",
                "run_attempt",
                "head_sha",
                "provider_api_version",
                "source_evidence_digest",
            )
        ),
        *(
            (field, 0)
            for field in (
                "installation_id",
                "repository_id",
                "workflow_run_id",
                "run_attempt",
            )
        ),
        ("head_sha", "a" * 39),
        ("provider_api_version", "2026-3-10"),
        ("source_evidence_digest", "b" * 63),
    ],
)
def test_runtime_insert_cannot_bypass_total_source_case_checks(
    runtime_postgres_database_url: str, field: str, value: object
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            source = _source(1001, now)
            policy = load_bundled_ci_economics_profile().collection_policy
            valid = encode_collection_record(
                initial_collection_state(source.source_id, now, now, policy), source
            )
            async with engine.begin() as connection:
                with pytest.raises(DBAPIError, match="ck_ci_workflow_attempt_collections_source"):
                    async with connection.begin_nested():
                        await connection.execute(
                            insert(ci_workflow_attempt_collections).values({**valid, field: value})
                        )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_collections)
                    )
                    == 0
                )
                await connection.execute(insert(ci_workflow_attempt_collections).values(valid))
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_collections)
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_registration_enforces_scope_quota_and_preserves_replay(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(engine)
            policy = load_bundled_ci_economics_profile().collection_policy
            limit = MAX_PROVIDER_SOURCES_PER_REPOSITORY
            sources = tuple(_source(run, now) for run in range(1, limit))
            records = [
                encode_collection_record(
                    initial_collection_state(source.source_id, now, now, policy), source
                )
                for source in sources
            ]
            async with engine.begin() as connection:
                await connection.execute(insert(ci_workflow_attempt_collections), records)
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def register_waiter() -> tuple[ProviderSourceRegistrationResult, ...]:
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    repository = transaction.ci_economics_collection
                    pid = await repository._connection.scalar(select(func.pg_backend_pid()))
                    assert type(pid) is int
                    waiter_pid.set_result(pid)
                    result = await repository.register_provider_sources(
                        sources[0].attempt.scope,
                        (_source(limit + 1, now), sources[0], _source(limit + 2, now)),
                    )
                    if "registered" in result:
                        await transaction.commit()
                    return result

            async with (
                asyncio.timeout(20),
                asyncio.TaskGroup() as group,
                PostgresCiEconomicsUnitOfWork(engine) as first,
            ):
                repository = first.ci_economics_collection
                assert (
                    await repository.register_provider_source(_source(limit, now)) == "registered"
                )
                holder = await repository._connection.scalar(select(func.pg_backend_pid()))
                assert type(holder) is int
                waiting = group.create_task(register_waiter())
                await wait_for_blocked_operation(
                    admin, holder, waiting, waiter_pid=await waiter_pid
                )
                await first.commit()
            assert waiting.result() == ("capacity_reached", "replayed", "capacity_reached")
            assert await store(engine).register_provider_source(sources[0]) == "replayed"
            assert (
                await store(engine).register_provider_source(
                    replace(sources[0], attempt=replace(sources[0].attempt, head_sha="d" * 40))
                )
                == "source_conflict"
            )
            assert (
                await store(engine).register_provider_source(
                    replace(sources[0], source_evidence_digest="c" * 64)
                )
                == "source_conflict"
            )
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_collections)
                    )
                    == limit
                )
            other_scope = replace(
                sources[0], attempt=replace(sources[0].attempt, scope=RepositoryScope(101, 203))
            )
            assert await store(engine).register_provider_source(other_scope) == "registered"
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_provider_natural_identity_cannot_be_reinserted_with_another_head(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            source = _source(303, now)
            alternate = replace(source, attempt=replace(source.attempt, head_sha="d" * 40))
            assert alternate.source_id != source.source_id
            assert await store(engine).register_provider_source(source) == "registered"
            assert await store(engine).register_provider_source(alternate) == "source_conflict"
            policy = load_bundled_ci_economics_profile().collection_policy
            alternate_row = encode_collection_record(
                initial_collection_state(alternate.source_id, now, now, policy), alternate
            )
            async with engine.begin() as connection:
                before = (
                    (await connection.execute(select(ci_workflow_attempt_collections)))
                    .mappings()
                    .all()
                )
                with pytest.raises(
                    DBAPIError, match="uq_ci_workflow_attempt_collections_provider_attempt"
                ):
                    async with connection.begin_nested():
                        await connection.execute(
                            insert(ci_workflow_attempt_collections).values(alternate_row)
                        )
                after = (
                    (await connection.execute(select(ci_workflow_attempt_collections)))
                    .mappings()
                    .all()
                )
                assert after == before and len(after) == 1
                assert after[0]["subject_id"] == source.source_id
        finally:
            await engine.dispose()

    asyncio.run(scenario())
