"""Process-lifetime ownership for external runtime resources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.integrations import GitHubActionsJwksProvider
from ci_coordinator.integrations.github import GitHubAppTransportFactory
from ci_coordinator.observability import BackgroundHealth, BackgroundHealthState


class RuntimeBackgroundService(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> bool: ...


@runtime_checkable
class RuntimeAsyncResource(Protocol):
    async def prepare(self) -> None: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class RuntimeBackgroundHealthProbe(Protocol):
    def background_health(self) -> BackgroundHealth: ...


class _LifecycleState(Enum):
    CONSTRUCTED = "constructed"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    CLOSED = "closed"
    CLOSE_FAILED = "close_failed"
    CLEANUP_PENDING = "cleanup_pending"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class _BackgroundDrainIncomplete(RuntimeError):
    pass


class _CleanupDeadlineExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class RuntimeResources:
    engine: AsyncEngine
    github: GitHubAppTransportFactory
    jwks: GitHubActionsJwksProvider
    background: RuntimeBackgroundService | None = None
    shutdown_timeout_seconds: float = 30.0
    additional_resources: tuple[RuntimeAsyncResource, ...] = ()
    _startup_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _close_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _cleanup_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _cleanup_deadline_reached: bool = field(default=False, init=False, repr=False)
    _background_start_attempted: bool = field(default=False, init=False, repr=False)
    _github_closed: bool = field(default=False, init=False, repr=False)
    _jwks_closed: bool = field(default=False, init=False, repr=False)
    _engine_closed: bool = field(default=False, init=False, repr=False)
    _additional_closed: set[int] = field(default_factory=set, init=False, repr=False)
    _lifecycle_state: _LifecycleState = field(
        default=_LifecycleState.CONSTRUCTED,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            type(self.shutdown_timeout_seconds) not in {int, float}
            or self.shutdown_timeout_seconds <= 0
        ):
            raise ValueError("runtime shutdown timeout must be positive")
        if (
            type(self.additional_resources) is not tuple
            or len(self.additional_resources) > 16
            or any(
                not isinstance(value, RuntimeAsyncResource) for value in self.additional_resources
            )
        ):
            raise ValueError("additional runtime resources must be a bounded exact tuple")
        self.shutdown_timeout_seconds = float(self.shutdown_timeout_seconds)

    @property
    def is_open(self) -> bool:
        return self._lifecycle_state is not _LifecycleState.CLOSED

    @property
    def cleanup_pending(self) -> bool:
        return self._lifecycle_state is _LifecycleState.CLEANUP_PENDING

    @property
    def lifecycle_ready(self) -> bool:
        return self._lifecycle_state is _LifecycleState.ACTIVE

    def background_health(self) -> BackgroundHealth:
        if self._lifecycle_state is _LifecycleState.STARTING:
            return BackgroundHealth(BackgroundHealthState.STARTING)
        if not self.lifecycle_ready:
            return BackgroundHealth(BackgroundHealthState.STOPPED)
        if self.background is None:
            return BackgroundHealth(BackgroundHealthState.NOT_CONFIGURED)
        if not isinstance(self.background, RuntimeBackgroundHealthProbe):
            return BackgroundHealth(BackgroundHealthState.UNOBSERVABLE)
        try:
            health = self.background.background_health()
        except Exception:
            return BackgroundHealth(BackgroundHealthState.UNOBSERVABLE)
        if not isinstance(health, BackgroundHealth):
            return BackgroundHealth(BackgroundHealthState.UNOBSERVABLE)
        return health

    @asynccontextmanager
    async def lifespan(self, _: FastAPI) -> AsyncIterator[None]:
        if self._lifecycle_state is not _LifecycleState.CONSTRUCTED:
            raise RuntimeError("runtime resource lifespan cannot be restarted")
        self._lifecycle_state = _LifecycleState.STARTING
        startup = asyncio.create_task(self._start_all())
        self._startup_task = startup
        primary_failure: BaseException | None = None
        try:
            try:
                await startup
            except asyncio.CancelledError:
                if self._lifecycle_state is not _LifecycleState.STARTING:
                    raise RuntimeError("runtime resource startup was interrupted") from None
                raise
            if self._startup_task is startup:
                self._startup_task = None
            self._require_startup_authority()
            self._lifecycle_state = _LifecycleState.ACTIVE
            yield
        except BaseException as error:
            primary_failure = error
            raise
        finally:
            try:
                await self.aclose()
            except BaseException:
                if primary_failure is None:
                    raise
                primary_failure.add_note("runtime resource cleanup also failed")

    async def _start_all(self) -> None:
        for resource in self.additional_resources:
            self._require_startup_authority()
            await resource.prepare()
            self._require_startup_authority()
        if self.background is not None:
            self._require_startup_authority()
            self._background_start_attempted = True
            await self.background.start()
            self._require_startup_authority()

    def _require_startup_authority(self) -> None:
        if self._lifecycle_state is not _LifecycleState.STARTING:
            raise RuntimeError("runtime resource startup was interrupted")

    async def aclose(self) -> None:
        if self._lifecycle_state is _LifecycleState.CLOSED:
            return
        if self._lifecycle_state in {
            _LifecycleState.CLEANUP_PENDING,
            _LifecycleState.DEADLINE_EXCEEDED,
        }:
            raise RuntimeError("runtime resource cleanup exceeded its deadline") from None
        finalizer = self._close_task
        if finalizer is None:
            self._lifecycle_state = _LifecycleState.STOPPING
            finalizer = asyncio.create_task(self._close_with_deadline())
            self._close_task = finalizer
        cancellation: asyncio.CancelledError | None = None
        while not finalizer.done():
            try:
                await asyncio.shield(finalizer)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except BaseException:
                break
        if finalizer.cancelled():
            self._lifecycle_state = _LifecycleState.CLOSE_FAILED
            if self._close_task is finalizer:
                self._close_task = None
            if cancellation is not None:
                cancellation.add_note("runtime resource cleanup was cancelled")
                raise cancellation
            raise RuntimeError("runtime resource cleanup failed") from None
        failure = finalizer.exception()
        if failure is not None:
            deadline_exceeded = isinstance(failure, _CleanupDeadlineExceeded)
            if deadline_exceeded:
                cleanup = self._cleanup_task
                self._lifecycle_state = (
                    _LifecycleState.CLEANUP_PENDING
                    if cleanup is not None and not cleanup.done()
                    else _LifecycleState.DEADLINE_EXCEEDED
                )
            elif self._close_task is finalizer:
                self._lifecycle_state = _LifecycleState.CLOSE_FAILED
                self._close_task = None
            if cancellation is not None:
                cancellation.add_note(
                    "runtime resource cleanup exceeded its deadline"
                    if deadline_exceeded
                    else "runtime resource cleanup also failed"
                )
            else:
                message = (
                    "runtime resource cleanup exceeded its deadline"
                    if deadline_exceeded
                    else (
                        "runtime background service did not drain"
                        if isinstance(failure, _BackgroundDrainIncomplete)
                        else "runtime resource cleanup failed"
                    )
                )
                raise RuntimeError(message) from None
        else:
            self._lifecycle_state = _LifecycleState.CLOSED
        if cancellation is not None:
            raise cancellation

    async def _close_with_deadline(self) -> None:
        cleanup = asyncio.create_task(self._close_all())
        self._cleanup_task = cleanup
        cleanup.add_done_callback(self._on_cleanup_settled)
        done, _pending = await asyncio.wait(
            {cleanup},
            timeout=self.shutdown_timeout_seconds,
        )
        if cleanup not in done:
            self._cleanup_deadline_reached = True
            cleanup.cancel()
            raise _CleanupDeadlineExceeded(
                "runtime resource cleanup exceeded its deadline"
            ) from None
        await cleanup

    def _on_cleanup_settled(self, task: asyncio.Task[None]) -> None:
        if self._cleanup_task is task:
            self._cleanup_task = None
        if self._lifecycle_state is _LifecycleState.CLEANUP_PENDING:
            self._lifecycle_state = _LifecycleState.DEADLINE_EXCEEDED
        _consume_task_outcome(task)

    async def _close_all(self) -> None:
        await self._quiesce_startup()
        self._raise_if_cleanup_deadline_reached()
        if self.background is not None and self._background_start_attempted:
            try:
                drained = await self.background.stop()
            except Exception:
                raise _BackgroundDrainIncomplete(
                    "runtime background service did not drain"
                ) from None
            if not drained:
                raise _BackgroundDrainIncomplete("runtime background service did not drain")
            self._raise_if_cleanup_deadline_reached()
        failure_count = 0
        for index, resource in enumerate(self.additional_resources):
            if index in self._additional_closed:
                continue
            try:
                await resource.aclose()
            except Exception:
                failure_count += 1
                self._raise_if_cleanup_deadline_reached()
            else:
                self._additional_closed.add(index)
                self._raise_if_cleanup_deadline_reached()
        for close, marker in (
            (self.github.aclose, "_github_closed"),
            (self.jwks.aclose, "_jwks_closed"),
            (self.engine.dispose, "_engine_closed"),
        ):
            if getattr(self, marker):
                continue
            try:
                await close()
            except Exception:
                failure_count += 1
                self._raise_if_cleanup_deadline_reached()
            else:
                setattr(self, marker, True)
                self._raise_if_cleanup_deadline_reached()
        if failure_count:
            raise RuntimeError("runtime resource cleanup failed") from None

    async def _quiesce_startup(self) -> None:
        startup = self._startup_task
        if startup is None:
            return
        if not startup.done():
            startup.cancel()
        try:
            await asyncio.shield(startup)
        except asyncio.CancelledError:
            if not startup.done():
                raise
            _consume_task_outcome(startup)
        except BaseException:
            _consume_task_outcome(startup)
        finally:
            if self._startup_task is startup and startup.done():
                self._startup_task = None

    def _raise_if_cleanup_deadline_reached(self) -> None:
        if self._cleanup_deadline_reached:
            raise asyncio.CancelledError


def _consume_task_outcome(task: asyncio.Task[None]) -> None:
    try:
        task.exception()
    except BaseException:
        return
