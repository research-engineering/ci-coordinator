from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ._reconciliation_support import database_time


async def wait_for_blocked_operation[T](
    engine: AsyncEngine,
    holder: int,
    operation: asyncio.Task[T],
    *,
    timeout_seconds: float = 3,
    waiter_pid: int | None = None,
) -> None:
    async with asyncio.timeout(timeout_seconds), engine.connect() as observer:
        while True:
            await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
            blocked = await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE (CAST(:waiter_pid AS integer) IS NULL OR pid = :waiter_pid) "
                    "AND :holder = ANY(pg_blocking_pids(pid)))"
                ),
                {"holder": holder, "waiter_pid": waiter_pid},
            )
            assert not operation.done(), (
                "operation completed before the intended lock",
                operation.result(),
            )
            if blocked:
                return
            await asyncio.sleep(0.01)


async def wait_for_database_deadline(
    engine: AsyncEngine, expires_at: datetime, *, timeout_seconds: float = 6
) -> None:
    async with asyncio.timeout(timeout_seconds):
        remaining = (expires_at - await database_time(engine)).total_seconds()
        assert 0 < remaining < timeout_seconds
        await asyncio.sleep(remaining + 0.02)
        assert await database_time(engine) >= expires_at
