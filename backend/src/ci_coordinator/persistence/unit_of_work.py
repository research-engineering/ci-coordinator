from __future__ import annotations

import asyncio
from enum import Enum
from math import isfinite
from types import TracebackType
from typing import Final, Self

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from ci_coordinator.audit_replay import AuditEventRepository
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_contracts import CapabilityDeclaration
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.connection import configure_read_committed, verify_read_committed
from ci_coordinator.persistence.errors import (
    CommitCancelledOutcomeUnknown,
    CommitOutcomeUnknown,
    CommittedButCleanupFailed,
    DatabaseCompatibilityError,
    StoreUnavailable,
)
from ci_coordinator.persistence.schema_capabilities import audit_ledger_requirements
from ci_coordinator.persistence.webhook_delivery_audit import (
    _PostgresWebhookDeliveryAuditRepository,
)

DEFAULT_UNIT_OF_WORK_CLEANUP_TIMEOUT_SECONDS: Final = 5.0


class _State(Enum):
    NEW = "new"
    ACTIVE = "active"
    ROLLBACK_REQUIRED = "rollback_required"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class _CleanupDeadlineExceeded(RuntimeError):
    pass


class PostgresUnitOfWork:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        cleanup_timeout_seconds: float = DEFAULT_UNIT_OF_WORK_CLEANUP_TIMEOUT_SECONDS,
        _required_capabilities: tuple[CapabilityDeclaration, ...] | None = None,
    ) -> None:
        if (
            type(cleanup_timeout_seconds) not in {int, float}
            or not isfinite(cleanup_timeout_seconds)
            or cleanup_timeout_seconds <= 0
        ):
            raise ValueError("unit-of-work cleanup timeout must be finite and positive")
        self._engine = engine
        self._cleanup_timeout_seconds = float(cleanup_timeout_seconds)
        self._profile = load_bundled_profile()
        self._state = _State.NEW
        self._owner_task: asyncio.Task[object] | None = None
        self._connection: AsyncConnection | None = None
        self._transaction: AsyncTransaction | None = None
        self._audit_events: _PostgresWebhookDeliveryAuditRepository | None = None
        self._required_capabilities = (
            audit_ledger_requirements(self._profile)
            if _required_capabilities is None
            else _required_capabilities
        )

    @property
    def audit_events(self) -> AuditEventRepository:
        self._require_active()
        return self._require_initialized(self._audit_events, "audit event repository")

    async def __aenter__(self) -> Self:
        if self._state is not _State.NEW:
            raise RuntimeError("unit of work cannot be reused")
        try:
            self._connection = await self._engine.connect()
            self._connection = await configure_read_committed(self._connection, self._profile)
            self._transaction = await self._connection.begin()
            await verify_read_committed(self._connection, self._profile)
            await acquire_compatibility_fence(
                self._connection,
                self._profile,
                CompatibilityFenceMode.PARTICIPANT,
            )
            await admit_schema_dependent_operation(
                self._connection,
                self._profile,
                self._required_capabilities,
            )
            self._owner_task = asyncio.current_task()
            self._initialize_repositories()
            self._state = _State.ACTIVE
        except asyncio.CancelledError as error:
            self._state = _State.FAILED
            await self._close_after_failed_enter(error)
            raise
        except DatabaseCompatibilityError as error:
            self._state = _State.FAILED
            await self._close_after_failed_enter(error)
            raise
        except Exception as error:
            self._state = _State.FAILED
            await self._close_after_failed_enter(error)
            raise StoreUnavailable("could not begin database transaction") from error
        return self

    def _initialize_repositories(self) -> None:
        connection = self._require_initialized(self._connection, "database connection")
        self._audit_events = _PostgresWebhookDeliveryAuditRepository(
            connection,
            self._require_active,
            self._mark_rollback_required,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, traceback
        self._require_owner()
        committed = self._state is _State.COMMITTED
        finalizer = asyncio.create_task(self._finalize())
        cancellation, cleanup_error = await self._await_finalizer(finalizer)
        if cleanup_error is not None:
            deadline_exceeded = isinstance(cleanup_error, _CleanupDeadlineExceeded)
            note = (
                "database finalization exceeded its deadline"
                if deadline_exceeded
                else f"database finalization also failed: {cleanup_error}"
            )
            if exc_value is not None:
                exc_value.add_note(note)
            elif cancellation is not None:
                cancellation.add_note(note)
            elif committed:
                if deadline_exceeded:
                    raise CommittedButCleanupFailed(
                        "database commit succeeded but connection cleanup failed"
                    ) from None
                raise CommittedButCleanupFailed(
                    "database commit succeeded but connection cleanup failed"
                ) from cleanup_error
            else:
                if deadline_exceeded:
                    raise StoreUnavailable("database finalization exceeded its deadline") from None
                raise StoreUnavailable("database finalization failed") from cleanup_error
        if cancellation is not None and exc_value is None:
            raise cancellation

    async def commit(self) -> None:
        self._require_active()
        transaction = self._require_initialized(self._transaction, "database transaction")
        try:
            await transaction.commit()
        except asyncio.CancelledError as error:
            self._state = _State.FAILED
            raise CommitCancelledOutcomeUnknown(
                "database commit was cancelled with an unknown outcome"
            ) from error
        except Exception as error:
            self._state = _State.FAILED
            raise CommitOutcomeUnknown(
                "database commit outcome is unknown; reconcile by idempotency key"
            ) from error
        self._state = _State.COMMITTED

    async def rollback(self) -> None:
        self._require_owner()
        if self._state not in {_State.ACTIVE, _State.ROLLBACK_REQUIRED}:
            raise RuntimeError("unit of work is not active")
        await self._rollback_transaction()

    async def _rollback_transaction(self) -> None:
        transaction = self._require_initialized(self._transaction, "database transaction")
        try:
            await transaction.rollback()
        except asyncio.CancelledError:
            self._state = _State.FAILED
            raise
        except Exception as error:
            self._state = _State.FAILED
            raise StoreUnavailable("database rollback failed") from error
        self._state = _State.ROLLED_BACK

    def _require_active(self) -> None:
        if self._state is not _State.ACTIVE:
            raise RuntimeError("unit of work is not active")
        self._require_owner()

    def _require_owner(self) -> None:
        if asyncio.current_task() is not self._owner_task:
            raise RuntimeError("unit of work cannot be shared across tasks")

    @staticmethod
    def _require_initialized[T](value: T | None, component: str) -> T:
        if value is None:
            raise RuntimeError(f"unit of work {component} is not initialized")
        return value

    def _mark_rollback_required(self) -> None:
        if self._state is _State.ACTIVE:
            self._state = _State.ROLLBACK_REQUIRED

    async def _close_connection(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _finalize(self) -> None:
        primary_error: BaseException | None = None
        try:
            if self._state in {_State.ACTIVE, _State.ROLLBACK_REQUIRED}:
                await self._rollback_transaction()
        except BaseException as error:
            primary_error = error
        try:
            await self._close_connection()
        except BaseException as error:
            if primary_error is None:
                primary_error = error
            else:
                primary_error.add_note(f"database connection cleanup also failed: {error}")
        if primary_error is not None:
            raise primary_error

    async def _close_after_failed_enter(self, primary_error: BaseException) -> None:
        closer = asyncio.create_task(self._rollback_and_close_after_failed_enter())
        cancellation, cleanup_error = await self._await_finalizer(closer)
        if cancellation is not None:
            primary_error.add_note("additional cancellation observed during database cleanup")
        if cleanup_error is not None:
            primary_error.add_note(
                "database connection cleanup exceeded its deadline"
                if isinstance(cleanup_error, _CleanupDeadlineExceeded)
                else f"database connection cleanup also failed: {cleanup_error}"
            )

    async def _await_finalizer(
        self,
        finalizer: asyncio.Task[None],
    ) -> tuple[asyncio.CancelledError | None, BaseException | None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cleanup_timeout_seconds
        cancellation: asyncio.CancelledError | None = None
        while not finalizer.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            wait = asyncio.timeout(remaining)
            try:
                async with wait:
                    await asyncio.shield(finalizer)
            except asyncio.CancelledError as error:
                if cancellation is None:
                    cancellation = error
                else:
                    cancellation.add_note(f"additional cancellation observed: {error}")
            except TimeoutError:
                if not wait.expired():
                    raise
                break
            except BaseException:
                break
        if not finalizer.done():
            self._state = _State.FAILED
            finalizer.cancel()
            finalizer.add_done_callback(_consume_task_outcome)
            return cancellation, _CleanupDeadlineExceeded("database cleanup exceeded its deadline")
        try:
            return cancellation, finalizer.exception()
        except asyncio.CancelledError as error:
            return cancellation, error

    async def _rollback_and_close_after_failed_enter(self) -> None:
        primary_error: BaseException | None = None
        if self._transaction is not None:
            try:
                await self._transaction.rollback()
            except BaseException as error:
                primary_error = error
            finally:
                self._transaction = None
        try:
            await self._close_connection()
        except BaseException as error:
            if primary_error is None:
                primary_error = error
            else:
                primary_error.add_note(f"database connection cleanup also failed: {error}")
        self._owner_task = None
        if primary_error is not None:
            raise primary_error


def _consume_task_outcome(task: asyncio.Task[None]) -> None:
    try:
        task.exception()
    except BaseException:
        return
