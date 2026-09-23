import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import archived_statistics
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    DetailPolicyReference,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryUsage
from ci_coordinator.kernel import SystemMonotonicClock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts as attempts,
)
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_details as details
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_jobs as jobs
from ci_coordinator.persistence.ci_history_detail_cleanup import (
    expire_history_details,
    expire_history_details_in_scope,
)
from ci_coordinator.persistence.ci_history_retention_codec import encode_history_retention
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    lock_history_scope,
    write_history_dataset,
)
from ci_coordinator.persistence.ci_history_statistics_store import store_history_statistics
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.runtime.history_detail_cleanup import (
    HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS,
    HistoryDetailCleanup,
)
from ci_coordinator.runtime.maintenance_round import (
    BoundedMaintenanceOperation,
    RuntimeMaintenanceRound,
)

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence
_ATTEMPT_ROWS = select(attempts).order_by(attempts.c.workflow_run_id, attempts.c.run_attempt)
_DETAIL_ROWS = select(details).order_by(details.c.workflow_run_id, details.c.run_attempt)
_JOB_ROWS = select(jobs).order_by(
    jobs.c.workflow_run_id, jobs.c.run_attempt, jobs.c.provider_job_id
)


async def _seed_details(
    engine: AsyncEngine, runtime: AsyncEngine, *, due: bool = True
) -> HistoryDataset:
    command = history_command()
    assert isinstance(await history_store(runtime).configure_history(command), HistoryConfigured)
    async with engine.begin() as connection:
        await lock_history_scope(connection, command.scope)
        dataset = await load_history_dataset(connection, command.scope, locked=True)
        assert dataset is not None
        now = await history_database_time(connection)
        first_import = now - timedelta(days=2)
        retention = ArchiveDetailRetention(
            "retained",
            first_import,
            DetailRetentionPolicy("days", 1 if due else 30),
            DetailPolicyReference("repository_override", 1),
        )
        total_bytes = 0
        for index in range(3):
            source = archived_statistics()
            source = source.model_copy(
                update={
                    "attempt": source.attempt.model_copy(update={"workflow_run_id": 303 + index})
                }
            )
            dataset, outcome = await store_history_statistics(connection, dataset, source, now=now)
            assert outcome == "recorded"
            raw = b'{"fixture":"' + b"x" * (index + 1) + b'"}'
            await connection.execute(
                update(attempts)
                .where(attempts.c.workflow_run_id == 303 + index)
                .values(**encode_history_retention(retention), first_imported_at=first_import)
            )
            await connection.execute(
                insert(details).values(
                    installation_id=dataset.scope.installation_id,
                    repository_id=dataset.scope.repository_id,
                    generation=dataset.generation,
                    workflow_run_id=303 + index,
                    run_attempt=1,
                    detail_canonical=raw,
                )
            )
            total_bytes += len(raw)
        usage = dataset.usage.reserve(
            HistoryUsage(attempts=0, jobs=0, gaps=0, canonicalBytes=total_bytes),
            dataset.configuration.quota,
        )
        assert usage is not None
        successor = replace(dataset, usage=usage, data_revision=dataset.data_revision + 1)
        await write_history_dataset(connection, dataset, successor)
        return successor


@pytest.mark.parametrize("paused", [False, True])
def test_expiry_is_bounded_preserves_statistics_and_releases_exact_bytes(
    postgres_database_url: str, runtime_postgres_database_url: str, paused: bool
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(admin, runtime)
            async with admin.begin() as connection:
                if paused:
                    changed = replace(
                        original,
                        state="paused",
                        configuration=original.configuration.model_copy(update={"enabled": False}),
                        configuration_revision=original.configuration_revision + 1,
                    )
                    await write_history_dataset(connection, original, changed)
                before = (await connection.execute(_ATTEMPT_ROWS)).mappings().all()
                original_jobs = (await connection.execute(_JOB_ROWS)).all()
                original_details = (await connection.execute(_DETAIL_ROWS)).mappings().all()
            async with runtime.begin() as connection:
                assert (
                    await expire_history_details_in_scope(connection, original.scope, limit=2) == 2
                )
            async with runtime.begin() as connection:
                after = (await connection.execute(_ATTEMPT_ROWS)).mappings().all()
                assert len(after) == len(before) == 3
                for old, current in zip(before, after, strict=True):
                    assert {key: value for key, value in old.items() if key != "detail_state"} == {
                        key: value for key, value in current.items() if key != "detail_state"
                    }
                assert {
                    row["workflow_run_id"] for row in after if row["detail_state"] == "expired"
                } == {303, 304}
                assert (await connection.execute(_JOB_ROWS)).all() == original_jobs
                remaining = (await connection.execute(_DETAIL_ROWS)).mappings().one()
                assert remaining["workflow_run_id"] == 305
                dataset = await load_history_dataset(connection, original.scope)
                assert dataset is not None
                removed_bytes = sum(
                    len(row["detail_canonical"])
                    for row in original_details
                    if row["workflow_run_id"] != 305
                )
                assert dataset.usage == original.usage.release(
                    HistoryUsage(attempts=0, jobs=0, gaps=0, canonicalBytes=removed_bytes)
                )
                assert dataset.data_revision == original.data_revision + 1
                assert await expire_history_details(connection) == 1
            async with runtime.begin() as connection:
                assert await expire_history_details(connection) == 0
                dataset = await load_history_dataset(connection, original.scope)
                assert dataset is not None and dataset.data_revision == original.data_revision + 2
                assert dataset.usage.attempts == dataset.usage.jobs == 3
                assert not (await connection.execute(_DETAIL_ROWS)).all()
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("paused", [False, True])
def test_runtime_maintenance_commits_expiry_after_startup_even_when_collection_is_paused(
    postgres_database_url: str, runtime_postgres_database_url: str, paused: bool
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(runtime)
        try:
            original = await _seed_details(admin, runtime)
            if paused:
                initial = history_command()
                command = ConfigureHistory.model_validate(
                    {
                        **initial.model_dump(),
                        "expectedRevision": original.configuration_revision,
                        "initialCreatedFrom": None,
                        "operationId": "pause-before-expiry",
                        "configuration": {
                            **initial.configuration.model_dump(),
                            "enabled": False,
                        },
                    }
                )
                assert isinstance(await store.configure_history(command), HistoryConfigured)
            async with runtime.connect() as connection:
                before = (await connection.execute(_ATTEMPT_ROWS)).mappings().all()
                original_jobs = (await connection.execute(_JOB_ROWS)).all()
                detail_rows = (await connection.execute(_DETAIL_ROWS)).mappings().all()
            calls = 0

            async def primary(_: asyncio.Event) -> None:
                nonlocal calls
                calls += 1

            maintenance = RuntimeMaintenanceRound(
                primary,
                (
                    BoundedMaintenanceOperation(
                        "ci_history_detail_cleanup",
                        HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS,
                        HistoryDetailCleanup(store.expire_details),
                    ),
                ),
                metrics=RuntimeMetrics(),
                clock=SystemMonotonicClock(),
            )
            abort = asyncio.Event()
            await maintenance(abort)
            async with runtime.connect() as connection:
                assert (await connection.execute(_DETAIL_ROWS)).mappings().all() == detail_rows
            await maintenance(abort)
            assert calls == 2
            async with runtime.connect() as connection:
                after = (await connection.execute(_ATTEMPT_ROWS)).mappings().all()
                assert len(after) == len(before) == 3
                for old, current in zip(before, after, strict=True):
                    assert current["detail_state"] == "expired"
                    assert {k: v for k, v in old.items() if k != "detail_state"} == {
                        k: v for k, v in current.items() if k != "detail_state"
                    }
                assert (await connection.execute(_JOB_ROWS)).all() == original_jobs
                assert not (await connection.execute(_DETAIL_ROWS)).all()
                dataset = await load_history_dataset(connection, original.scope)
                assert dataset is not None
                assert dataset.state == ("paused" if paused else "active")
                assert dataset.usage == original.usage.release(
                    HistoryUsage(
                        attempts=0,
                        jobs=0,
                        gaps=0,
                        canonicalBytes=sum(len(row["detail_canonical"]) for row in detail_rows),
                    )
                )
                assert dataset.data_revision == original.data_revision + 1
            assert await store.expire_details() == 0
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["missing_child", "quota_underflow", "outer_rollback"])
def test_cleanup_failure_preserves_all_other_rows_and_retention(
    postgres_database_url: str, runtime_postgres_database_url: str, failure: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(engine, runtime)
            async with engine.begin() as connection:
                if failure == "missing_child":
                    await connection.execute(
                        delete(details).where(details.c.workflow_run_id == 304)
                    )
                if failure == "quota_underflow":
                    await write_history_dataset(
                        connection,
                        original,
                        replace(
                            original, usage=original.usage.model_copy(update={"canonical_bytes": 0})
                        ),
                    )
                before = (await connection.execute(_ATTEMPT_ROWS)).all()
                children = (await connection.execute(_DETAIL_ROWS)).all()
                dataset_before = await load_history_dataset(connection, original.scope)
            if failure == "outer_rollback":
                with pytest.raises(RuntimeError, match="rollback witness"):
                    async with runtime.begin() as connection:
                        assert await expire_history_details(connection) == 3
                        raise RuntimeError("rollback witness")
            else:
                async with runtime.begin() as connection:
                    with pytest.raises(ValueError):
                        await expire_history_details_in_scope(connection, original.scope)
                    assert (await connection.execute(_ATTEMPT_ROWS)).all() == before
                    assert (await connection.execute(_DETAIL_ROWS)).all() == children
            async with engine.connect() as connection:
                assert (await connection.execute(_ATTEMPT_ROWS)).all() == before
                assert (await connection.execute(_DETAIL_ROWS)).all() == children
                assert await load_history_dataset(connection, original.scope) == dataset_before
        finally:
            await runtime.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["future", "forever", "generation", "erasing"])
def test_cleanup_does_not_expire_ineligible_detail(
    postgres_database_url: str, runtime_postgres_database_url: str, mode: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(engine, runtime, due=mode != "future")
            async with engine.begin() as connection:
                if mode == "forever":
                    now = await history_database_time(connection)
                    retention = ArchiveDetailRetention(
                        "retained",
                        now - timedelta(days=2),
                        DetailRetentionPolicy("forever"),
                        DetailPolicyReference("repository_override", 2),
                    )
                    await connection.execute(
                        update(attempts).values(**encode_history_retention(retention))
                    )
                if mode in {"generation", "erasing"}:
                    await write_history_dataset(
                        connection,
                        original,
                        replace(original, generation=2)
                        if mode == "generation"
                        else replace(
                            original,
                            state="erasing",
                            configuration=original.configuration.model_copy(
                                update={"enabled": False}
                            ),
                        ),
                    )
                before = (await connection.execute(_ATTEMPT_ROWS)).all()
                children = (await connection.execute(_DETAIL_ROWS)).all()
            async with runtime.begin() as connection:
                assert await expire_history_details(connection) == 0
                assert await expire_history_details_in_scope(connection, original.scope) == 0
                assert (await connection.execute(_ATTEMPT_ROWS)).all() == before
                assert (await connection.execute(_DETAIL_ROWS)).all() == children
        finally:
            await runtime.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("commit", [False, True])
def test_competing_cleanup_cannot_double_release_uncommitted_bytes(
    postgres_database_url: str, runtime_postgres_database_url: str, commit: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(engine, runtime)
            async with runtime.connect() as holder:
                transaction = await holder.begin()
                try:
                    assert await expire_history_details_in_scope(holder, original.scope) == 3
                    async with runtime.begin() as competitor:
                        assert (
                            await expire_history_details_in_scope(competitor, original.scope) == 0
                        )
                        assert await load_history_dataset(competitor, original.scope) == original
                    if commit:
                        await transaction.commit()
                    else:
                        await transaction.rollback()
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
            async with runtime.begin() as successor:
                assert await expire_history_details_in_scope(successor, original.scope) == (
                    0 if commit else 3
                )
                dataset = await load_history_dataset(successor, original.scope)
                assert dataset is not None
                assert dataset.data_revision == original.data_revision + 1
                assert dataset.usage.attempts == dataset.usage.jobs == 3
                assert not (await successor.execute(_DETAIL_ROWS)).all()
                assert len((await successor.execute(_JOB_ROWS)).all()) == 3
        finally:
            await runtime.dispose()
            await engine.dispose()

    asyncio.run(scenario())
