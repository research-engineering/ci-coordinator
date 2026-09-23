from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence import PostgresShadowReconciliationUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.reconciliation_state_repository import (
    _PostgresReconciliationRepository,
)
from ci_coordinator.persistence.schema import (
    audit_events,
    reconciliation_observations,
    reconciliation_results,
    reconciliation_subjects,
    shadow_evidence,
)
from ci_coordinator.reconciliation import (
    ReconciliationAttemptClaim,
    ReconciliationClaimLost,
    ReconciliationResult,
)

from ._reconciliation_support import (
    POLICY,
    claim,
    database_time,
    expire_claim,
    observation,
    register,
    subject,
)

pytestmark = pytest.mark.persistence
type Operation = Literal["append", "defer", "terminal", "snapshot"]


@pytest.mark.parametrize("operation", ["append", "defer", "terminal", "snapshot"])
def test_blocked_operation_checks_database_time_after_the_row_lock(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    operation: Operation,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            target = subject()
            await register(engine, target)
            acquired = await claim(engine, "1" * 64, policy=replace(POLICY, lease_seconds=5))
            assert acquired is not None
            before = await _retained(engine)
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def attempt() -> object:
                async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                    repository = transaction.reconciliation
                    assert (
                        await repository._connection.scalar(
                            select(func.set_config("lock_timeout", "15s", True))
                        )
                        == "15s"
                    )
                    pid = await repository._connection.scalar(select(func.pg_backend_pid()))
                    assert type(pid) is int
                    waiter_pid.set_result(pid)
                    outcome = await _transition(repository, acquired, operation)
                    await transaction.commit()
                    return outcome

            async with asyncio.timeout(20), asyncio.TaskGroup() as group:
                async with admin.begin() as holder:
                    await holder.execute(select(reconciliation_subjects).with_for_update())
                    holder_pid = await holder.scalar(select(func.pg_backend_pid()))
                    assert type(holder_pid) is int
                    pending = group.create_task(attempt())
                    await _wait_for_lock(admin, await waiter_pid, holder_pid)
                    assert await database_time(admin) < acquired.lease_expires_at
                    await _wait_until_expired(admin, acquired.lease_expires_at)
                assert isinstance(await pending, ReconciliationClaimLost)
            assert await _retained(engine) == before
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["append", "defer", "terminal"])
def test_cas_rechecks_expiry_even_when_the_domain_sample_was_valid(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            await register(engine, subject())
            acquired = await claim(engine, "1" * 64)
            assert acquired is not None
            expired = await expire_claim(admin, acquired)
            assert expired.lease_expires_at < await database_time(admin) < expired.deadline_at
            before = await _retained(engine)

            async def stale_sample(_repository: _PostgresReconciliationRepository) -> datetime:
                return expired.claimed_at

            monkeypatch.setattr(_PostgresReconciliationRepository, "_database_time", stale_sample)
            async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                outcome = await _transition(transaction.reconciliation, expired, operation)
                assert isinstance(outcome, ReconciliationClaimLost)
                await transaction.commit()
            assert await _retained(engine) == before
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_cancellation_while_waiting_for_the_subject_lock_cannot_commit(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            await register(engine, subject())
            acquired = await claim(engine, "1" * 64)
            assert acquired is not None
            before = await _retained(engine)
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def attempt() -> None:
                async with PostgresShadowReconciliationUnitOfWork(engine) as transaction:
                    pid = await transaction.reconciliation._connection.scalar(
                        select(func.pg_backend_pid())
                    )
                    assert type(pid) is int
                    waiter_pid.set_result(pid)
                    await _transition(transaction.reconciliation, acquired, "terminal")
                    await transaction.commit()

            async with asyncio.timeout(20), asyncio.TaskGroup() as group, admin.begin() as holder:
                await holder.execute(select(reconciliation_subjects).with_for_update())
                holder_pid = await holder.scalar(select(func.pg_backend_pid()))
                assert type(holder_pid) is int
                pending = group.create_task(attempt())
                await _wait_for_lock(admin, await waiter_pid, holder_pid)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert await _retained(engine) == before
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


async def _transition(
    repository: _PostgresReconciliationRepository,
    acquired: ReconciliationAttemptClaim,
    operation: Operation,
) -> object:
    if operation == "append":
        return await repository.append_observation(
            acquired, 0, observation(acquired.subject, "job-1")
        )
    if operation == "defer":
        return await repository.defer_claim(acquired)
    if operation == "snapshot":
        return await repository.load_snapshot(acquired)
    return await repository.record_result(
        acquired, 0, ReconciliationResult(acquired.subject.subject_id, "success", ())
    )


async def _wait_until_expired(engine: AsyncEngine, expiry: datetime) -> None:
    while True:
        if await database_time(engine) >= expiry:
            return
        await asyncio.sleep(0.01)


async def _wait_for_lock(engine: AsyncEngine, waiting_pid: int, holder_pid: int) -> None:
    async with engine.connect() as connection:
        while True:
            blockers = await connection.scalar(select(func.pg_blocking_pids(waiting_pid)))
            assert blockers is not None
            if holder_pid in blockers:
                return
            await asyncio.sleep(0.01)


async def _retained(engine: AsyncEngine) -> tuple[dict[str, object], tuple[int, ...]]:
    async with engine.connect() as connection:
        state = (await connection.execute(select(reconciliation_subjects))).mappings().one()
        counts: list[int] = []
        for relation in (
            reconciliation_observations,
            reconciliation_results,
            audit_events,
            shadow_evidence,
        ):
            value = await connection.scalar(select(func.count()).select_from(relation))
            assert type(value) is int
            counts.append(value)
        return dict(state), tuple(counts)
