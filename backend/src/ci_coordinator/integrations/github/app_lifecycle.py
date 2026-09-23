"""Linearizable send and close lifecycle for the shared GitHub HTTP resource."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum


class _LifecycleState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class _SharedClientLifecycle:
    def __init__(self) -> None:
        self._state = _LifecycleState.OPEN
        self._active_sends = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    @property
    def is_open(self) -> bool:
        return self._state is _LifecycleState.OPEN

    async def enter_send(self) -> bool:
        async with self._lock:
            if self._state is not _LifecycleState.OPEN:
                return False
            self._active_sends += 1
            self._idle.clear()
            return True

    async def leave_send(self) -> None:
        async with self._lock:
            self._active_sends -= 1
            if self._active_sends < 0:
                raise RuntimeError("GitHub App lifecycle active-send count is invalid")
            if self._active_sends == 0:
                self._idle.set()

    async def close(self, close_resource: Callable[[], Awaitable[None]]) -> None:
        async with self._lock:
            if self._close_task is None:
                self._state = _LifecycleState.CLOSING
                self._close_task = asyncio.create_task(self._drain_and_close(close_resource))
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _drain_and_close(self, close_resource: Callable[[], Awaitable[None]]) -> None:
        try:
            await self._idle.wait()
            await close_resource()
        except BaseException:
            async with self._lock:
                if self._close_task is asyncio.current_task():
                    self._close_task = None
            raise
        async with self._lock:
            self._state = _LifecycleState.CLOSED
