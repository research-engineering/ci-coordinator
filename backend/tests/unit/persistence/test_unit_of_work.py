from __future__ import annotations

import asyncio
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.persistence import (
    CommitCancelledOutcomeUnknown,
    CommitOutcomeUnknown,
    CommittedButCleanupFailed,
    PostgresUnitOfWork,
    StoreUnavailable,
)
from ci_coordinator.persistence import unit_of_work as unit_of_work_module


class _FakeTransaction:
    def __init__(
        self,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.committed = False
        self.rolled_back = False

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    async def rollback(self) -> None:
        if self.rollback_error is not None:
            raise self.rollback_error
        self.rolled_back = True


class _FakeConnection:
    def __init__(
        self,
        transaction: _FakeTransaction,
        begin_error: BaseException | None = None,
        close_error: BaseException | None = None,
        execute_errors: list[BaseException | None] | None = None,
    ) -> None:
        self.transaction = transaction
        self.begin_error = begin_error
        self.close_error = close_error
        self.execute_errors = execute_errors or []
        self.closed = False

    async def begin(self) -> _FakeTransaction:
        if self.begin_error is not None:
            raise self.begin_error
        return self.transaction

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    async def execute(self, _statement: object) -> None:
        if self.execute_errors:
            error = self.execute_errors.pop(0)
            if error is not None:
                raise error


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def connect(self) -> _FakeConnection:
        return self.connection


@pytest.fixture(autouse=True)
def isolate_lifecycle_from_database_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    async def configure(connection: _FakeConnection, _profile: object) -> _FakeConnection:
        return connection

    async def verify(_connection: _FakeConnection, _profile: object) -> None:
        return None

    async def acquire(_connection: _FakeConnection, _profile: object, _mode: object) -> None:
        return None

    async def admit(_connection: _FakeConnection, _profile: object, _required: object) -> None:
        return None

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(unit_of_work_module, "configure_read_committed", configure)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(unit_of_work_module, "verify_read_committed", verify)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(unit_of_work_module, "acquire_compatibility_fence", acquire)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(unit_of_work_module, "admit_schema_dependent_operation", admit)


def test_explicit_rollback_is_terminal_and_closes_connection() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        async with unit_of_work:
            await unit_of_work.rollback()
            with pytest.raises(RuntimeError, match="not active"):
                await unit_of_work.commit()
        assert transaction.rolled_back
        assert connection.closed
        with pytest.raises(RuntimeError, match="cannot be reused"):
            await unit_of_work.__aenter__()

    asyncio.run(scenario())


def test_commit_failure_is_typed_as_unknown_and_not_rolled_back() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction(RuntimeError("lost acknowledgement"))
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        with pytest.raises(CommitOutcomeUnknown, match="reconcile by idempotency key"):
            async with unit_of_work:
                await unit_of_work.commit()
        assert not transaction.rolled_back
        assert connection.closed

    asyncio.run(scenario())


def test_commit_cancellation_preserves_asyncio_cancellation_type() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction(asyncio.CancelledError())
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        with pytest.raises(CommitCancelledOutcomeUnknown):
            async with unit_of_work:
                await unit_of_work.commit()
        assert not transaction.rolled_back
        assert connection.closed

    asyncio.run(scenario())


def test_begin_cancellation_closes_acquired_connection_and_propagates() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction, asyncio.CancelledError())
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        with pytest.raises(asyncio.CancelledError):
            await unit_of_work.__aenter__()
        assert connection.closed

    asyncio.run(scenario())


def test_repository_initialization_failure_rolls_back_and_closes_connection() -> None:
    class FailingRepositoryUnitOfWork(PostgresUnitOfWork):
        def _initialize_repositories(self) -> None:
            raise RuntimeError("repository construction failed")

    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction)
        unit_of_work = FailingRepositoryUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        with pytest.raises(StoreUnavailable, match="could not begin database transaction"):
            await unit_of_work.__aenter__()
        assert transaction.rolled_back
        assert connection.closed

    asyncio.run(scenario())


def test_committed_cleanup_failure_has_unambiguous_committed_outcome() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction, close_error=OSError("pool return failed"))
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        with pytest.raises(CommittedButCleanupFailed, match="commit succeeded"):
            async with unit_of_work:
                await unit_of_work.commit()
        assert transaction.committed

    asyncio.run(scenario())


def test_body_cancellation_survives_rollback_failure() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction(rollback_error=RuntimeError("rollback failed"))
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        with pytest.raises(asyncio.CancelledError):
            async with unit_of_work:
                raise asyncio.CancelledError
        assert connection.closed

    asyncio.run(scenario())


def test_repository_capability_is_task_bound_and_terminal() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        async with unit_of_work:
            repository = unit_of_work.audit_events
            child = asyncio.create_task(repository.snapshot())
            with pytest.raises(RuntimeError, match="shared across tasks"):
                await child
        with pytest.raises(RuntimeError, match="not active"):
            await repository.snapshot()

    asyncio.run(scenario())


def test_public_rollback_is_task_bound() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        async with unit_of_work:
            child = asyncio.create_task(unit_of_work.rollback())
            with pytest.raises(RuntimeError, match="shared across tasks"):
                await child
            assert not transaction.rolled_back
            await unit_of_work.rollback()
        assert transaction.rolled_back

    asyncio.run(scenario())


def test_compatibility_fence_precedes_schema_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        events: list[str] = []
        transaction = _FakeTransaction()
        connection = _FakeConnection(transaction)

        async def configure(
            configured_connection: _FakeConnection,
            _profile: object,
        ) -> _FakeConnection:
            events.append("configure")
            return configured_connection

        async def verify(_connection: _FakeConnection, _profile: object) -> None:
            events.append("verify")

        async def acquire(_connection: _FakeConnection, _profile: object, _mode: object) -> None:
            events.append("fence")

        async def admit(_connection: _FakeConnection, _profile: object, _required: object) -> None:
            events.append("admit")

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(unit_of_work_module, "configure_read_committed", configure)
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(unit_of_work_module, "verify_read_committed", verify)
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(unit_of_work_module, "acquire_compatibility_fence", acquire)
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(unit_of_work_module, "admit_schema_dependent_operation", admit)

        async with PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection))) as unit_of_work:
            assert events == ["configure", "verify", "fence", "admit"]
            await unit_of_work.rollback()

    asyncio.run(scenario())


def test_statement_cancellation_requires_rollback() -> None:
    async def scenario() -> None:
        transaction = _FakeTransaction()
        connection = _FakeConnection(
            transaction,
            execute_errors=[asyncio.CancelledError("statement cancelled")],
        )
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
        async with unit_of_work:
            with pytest.raises(asyncio.CancelledError, match="statement cancelled"):
                await unit_of_work.audit_events.snapshot()
            with pytest.raises(RuntimeError, match="not active"):
                await unit_of_work.commit()
        assert transaction.rolled_back

    asyncio.run(scenario())


def test_repeated_cancellation_preserves_first_reason_and_finishes_cleanup() -> None:
    class BlockingRollbackTransaction(_FakeTransaction):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def rollback(self) -> None:
            self.started.set()
            await self.release.wait()
            await super().rollback()

    async def scenario() -> None:
        transaction = BlockingRollbackTransaction()
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))

        async def worker() -> None:
            async with unit_of_work:
                pass

        task = asyncio.create_task(worker())
        await transaction.started.wait()
        task.cancel("first-cancel")
        await asyncio.sleep(0)
        task.cancel("second-cancel")
        transaction.release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("first-cancel",)
        assert transaction.rolled_back
        assert connection.closed

    asyncio.run(scenario())


def test_cleanup_deadline_bounds_a_cancellation_suppressing_driver() -> None:
    class CancellationSuppressingRollback(_FakeTransaction):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def rollback(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()
                self.cancelled.set()
                await self.release.wait()

    async def scenario() -> None:
        transaction = CancellationSuppressingRollback()
        connection = _FakeConnection(transaction)
        unit_of_work = PostgresUnitOfWork(
            cast(AsyncEngine, _FakeEngine(connection)),
            cleanup_timeout_seconds=0.001,
        )

        with pytest.raises(StoreUnavailable, match="exceeded its deadline"):
            async with unit_of_work:
                pass

        await transaction.cancelled.wait()
        transaction.release.set()
        await asyncio.sleep(0)

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_cleanup_deadline_rejects_nonfinite_values(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        PostgresUnitOfWork(cast(AsyncEngine, object()), cleanup_timeout_seconds=timeout)
