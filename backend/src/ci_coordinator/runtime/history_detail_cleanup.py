import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS: Final = 10


class HistoryDetailCleanup:
    def __init__(self, expire: Callable[[], Awaitable[int]]) -> None:
        self._expire = expire

    async def __call__(self, abort_signal: asyncio.Event, /) -> None:
        if not abort_signal.is_set():
            await self._expire()
