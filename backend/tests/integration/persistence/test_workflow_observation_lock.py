import asyncio
from typing import Literal

import pytest
from sqlalchemy import event, func, select

from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.workflow_observation_lock import (
    lock_workflow_observation,
    try_lock_workflow_observations,
)

from ._temporal_lock_support import wait_for_blocked_operation

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("completion", ["commit", "rollback"])
def test_observation_guard_serializes_exact_identity_until_transaction_completion(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    completion: Literal["commit", "rollback"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        observer = create_postgres_engine(postgres_database_url)
        try:
            async with (
                asyncio.timeout(10),
                engine.connect() as holder,
                engine.connect() as waiter,
            ):
                await lock_workflow_observation(holder, "delivery-a")
                await lock_workflow_observation(holder, "delivery-a")
                assert await try_lock_workflow_observations(
                    waiter, ("delivery-b", "delivery-a")
                ) == ("delivery-b",)
                pid = await holder.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                async with asyncio.TaskGroup() as group:
                    blocked = group.create_task(lock_workflow_observation(waiter, "delivery-a"))
                    await wait_for_blocked_operation(observer, pid, blocked)
                    if completion == "commit":
                        await holder.commit()
                    else:
                        await holder.rollback()
                await waiter.commit()
                assert await try_lock_workflow_observations(
                    holder, ("delivery-b", "delivery-a")
                ) == ("delivery-a", "delivery-b")
        finally:
            await observer.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_observation_batch_uses_one_bounded_query_and_skips_busy_identities(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        queries: list[object] = []

        def count_query(*arguments: object) -> None:
            queries.append(arguments[2])

        try:
            async with engine.connect() as holder, engine.connect() as collector:
                await lock_workflow_observation(holder, "delivery-0007")
                await collector.begin()
                event.listen(collector.sync_connection, "before_cursor_execute", count_query)
                assert await try_lock_workflow_observations(collector, ()) == ()
                assert queries == []
                identities = tuple(f"delivery-{index:04d}" for index in range(1_000))
                acquired = await try_lock_workflow_observations(collector, identities)
                assert acquired == tuple(value for value in identities if value != "delivery-0007")
                assert len(queries) == 1
                await holder.rollback()
                assert await try_lock_workflow_observations(collector, ("delivery-0007",)) == (
                    "delivery-0007",
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
