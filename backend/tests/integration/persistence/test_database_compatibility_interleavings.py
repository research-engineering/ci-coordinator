from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence import PostgresUnitOfWork
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.connection import (
    configure_read_committed,
    create_postgres_engine,
    verify_read_committed,
)

pytestmark = pytest.mark.persistence


def test_two_admitted_participants_share_the_compatibility_fence(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(engine) as first:
                await asyncio.wait_for(_enter_then_rollback(engine), timeout=1)
                await first.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_exclusive_migration_fence_waits_for_an_admitted_participant(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresUnitOfWork(runtime_engine) as participant:
                async with migration_engine.connect() as migration_raw:
                    migration = await configure_read_committed(migration_raw, profile)
                    transaction = await migration.begin()
                    try:
                        await verify_read_committed(migration, profile)
                        available = await migration.scalar(
                            text("SELECT pg_try_advisory_xact_lock(:class_id, :object_id)"),
                            {
                                "class_id": profile.fence_class_id,
                                "object_id": profile.fence_object_id,
                            },
                        )
                        assert available is False
                    finally:
                        await transaction.rollback()
                await participant.rollback()
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


def test_participant_waits_for_exclusive_migration_fence_then_admits(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        profile = load_bundled_profile()
        migration_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with (
                asyncio.TaskGroup() as participants,
                migration_engine.connect() as migration_raw,
            ):
                migration = await configure_read_committed(migration_raw, profile)
                transaction = await migration.begin()
                try:
                    await verify_read_committed(migration, profile)
                    await acquire_compatibility_fence(
                        migration,
                        profile,
                        CompatibilityFenceMode.MIGRATION,
                    )
                    entering = participants.create_task(_enter_then_rollback(runtime_engine))
                    await _wait_until_participant_is_blocked(migration_engine, entering)
                    await transaction.commit()
                    await asyncio.wait_for(entering, timeout=2)
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
        finally:
            await runtime_engine.dispose()
            await migration_engine.dispose()

    asyncio.run(scenario())


async def _enter_then_rollback(engine: AsyncEngine) -> None:
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await unit_of_work.rollback()


async def _wait_until_participant_is_blocked(
    engine: AsyncEngine,
    task: asyncio.Task[None],
) -> None:
    async with asyncio.timeout(2):
        while True:
            if task.done():
                raise AssertionError(
                    "participant did not wait for the exclusive compatibility fence"
                )
            async with engine.connect() as observer:
                blocked = await observer.scalar(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' "
                        "AND query LIKE 'SELECT pg_catalog.pg_advisory_xact_lock_shared(%')"
                    )
                )
            if blocked:
                return
            await asyncio.sleep(0.01)
