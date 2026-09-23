import asyncio
from unittest.mock import AsyncMock

import pytest

from ci_coordinator.runtime.history_detail_cleanup import HistoryDetailCleanup


@pytest.mark.parametrize("aborted", [False, True])
def test_cleanup_runs_one_bounded_store_operation_unless_aborted(aborted: bool) -> None:
    expire = AsyncMock(return_value=3)
    abort = asyncio.Event()
    if aborted:
        abort.set()

    asyncio.run(HistoryDetailCleanup(expire)(abort))

    if aborted:
        expire.assert_not_awaited()
    else:
        expire.assert_awaited_once_with()


def test_cleanup_propagates_storage_failure_to_maintenance_observation() -> None:
    failure = RuntimeError("storage unavailable")
    expire = AsyncMock(side_effect=failure)

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(HistoryDetailCleanup(expire)(asyncio.Event()))

    assert raised.value is failure


def test_cleanup_cancellation_waits_for_store_unwind() -> None:
    async def scenario() -> None:
        entered, unwound = asyncio.Event(), asyncio.Event()

        async def expire() -> int:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                unwound.set()
            return 0

        task = asyncio.create_task(HistoryDetailCleanup(expire)(asyncio.Event()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert unwound.is_set()

    asyncio.run(scenario())
