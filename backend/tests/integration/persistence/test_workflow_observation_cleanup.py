import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal

import pytest
from sqlalchemy import DateTime, literal, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.github_ingestion import DeliveryDuplicate
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.persistence import (
    PostgresCiEconomicsUnitOfWork,
    PostgresWebhookIngestionUnitOfWork,
)
from ci_coordinator.persistence import ci_economics_repository as storage
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_workflow_observations
from ci_coordinator.persistence.workflow_observation_lock import (
    lock_workflow_observation,
    try_lock_workflow_observations,
)

from ._ci_economics_support import database_now
from ._workflow_observation_support import seed_workflow_observation, workflow_observation

pytestmark = pytest.mark.persistence


def test_source_cleanup_includes_exact_cutoff_but_excludes_its_successor(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            cutoff = await database_now(admin) - timedelta(seconds=1)
            async with admin.begin() as connection:
                for identity, shift in (("before", -1), ("equal", 0), ("after", 1)):
                    await seed_workflow_observation(
                        connection,
                        workflow_observation(identity, cutoff),
                        cutoff + timedelta(microseconds=shift),
                    )
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(
                storage,
                "func",
                SimpleNamespace(
                    statement_timestamp=lambda: literal(cutoff, DateTime(timezone=True))
                ),
            )
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert await transaction.ci_economics.delete_expired_observations(limit=3) == 2
                await transaction.commit()
            async with engine.connect() as connection:
                assert tuple(
                    await connection.scalars(select(ci_workflow_observations.c.delivery_id))
                ) == ("after",)
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("completion", ["commit", "rollback"])
def test_source_cleanup_obeys_limit_expiry_and_transaction_under_runtime_role(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    completion: Literal["commit", "rollback"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(admin)
            async with admin.begin() as connection:
                for identity, remaining in (("a", -2), ("b", -1), ("live", 60)):
                    await seed_workflow_observation(
                        connection,
                        workflow_observation(identity, now),
                        now + timedelta(seconds=remaining),
                    )
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert await transaction.ci_economics.delete_expired_observations(limit=1) == 1
                if completion == "commit":
                    await transaction.commit()
                else:
                    await transaction.rollback()
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert await transaction.ci_economics.delete_expired_observations(limit=10) == (
                    1 if completion == "commit" else 2
                )
                await transaction.commit()
            async with engine.connect() as connection:
                assert tuple(
                    await connection.scalars(select(ci_workflow_observations.c.delivery_id))
                ) == ("live",)
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_source_cleanup_skips_guarded_source_without_blocking_next_candidate(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(admin)
            async with admin.begin() as connection:
                for identity in ("a", "b"):
                    await seed_workflow_observation(
                        connection, workflow_observation(identity, now), now - timedelta(seconds=1)
                    )
            async with engine.connect() as holder, asyncio.timeout(10):
                await lock_workflow_observation(holder, "a")
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    assert await transaction.ci_economics.delete_expired_observations(limit=2) == 1
                    await transaction.commit()
                assert tuple(
                    await holder.scalars(select(ci_workflow_observations.c.delivery_id))
                ) == ("a",)
                await holder.rollback()
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert await transaction.ci_economics.delete_expired_observations(limit=2) == 1
                await transaction.commit()
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_source_cleanup_rechecks_expiry_after_duplicate_recreates_selected_identity(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        selected = asyncio.Event()
        resume = asyncio.Event()

        async def pause_first_batch(
            connection: AsyncConnection, delivery_ids: tuple[str, ...]
        ) -> tuple[str, ...]:
            if not selected.is_set():
                selected.set()
                await resume.wait()
            return await try_lock_workflow_observations(connection, delivery_ids)

        async def cleanup() -> int:
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                deleted = await transaction.ci_economics.delete_expired_observations(limit=1)
                await transaction.commit()
                return deleted

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(storage, "try_lock_workflow_observations", pause_first_batch)
        try:
            now = await database_now(admin)
            observation = workflow_observation("recreated", now)
            async with admin.begin() as connection:
                await seed_workflow_observation(connection, observation, now - timedelta(seconds=1))
            async with asyncio.timeout(15), asyncio.TaskGroup() as group:
                stale_cleanup = group.create_task(cleanup())
                await selected.wait()
                try:
                    assert await cleanup() == 1
                    async with PostgresWebhookIngestionUnitOfWork(engine) as transaction:
                        result = await transaction.webhook_ingestion.commit(
                            prepare_delivery_claim(observation.provenance), observation
                        )
                        assert isinstance(result, DeliveryDuplicate)
                        await transaction.commit()
                finally:
                    resume.set()
            assert stale_cleanup.result() == 0
            async with engine.connect() as connection:
                expiry = await connection.scalar(select(ci_workflow_observations.c.retain_until))
                assert expiry is not None and expiry > now
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
