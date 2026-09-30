from __future__ import annotations

import asyncio
import traceback
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

import ci_coordinator.persistence.readiness as readiness_module
from ci_coordinator.persistence import (
    DatabaseReadiness,
    DatabaseReadinessProbe,
    check_database_readiness,
)


@pytest.mark.parametrize("audit_batch_size", [1, 4, 4096])
def test_database_readiness_audit_budget_is_constructor_owned_and_read_only(
    tmp_path: Path, audit_batch_size: int
) -> None:
    probe = DatabaseReadinessProbe(
        cast(AsyncEngine, object()), tmp_path / "alembic.ini", audit_batch_size=audit_batch_size
    )
    assert probe.audit_batch_size == audit_batch_size
    field = "audit_batch_size"
    with pytest.raises(AttributeError):
        setattr(probe, field, 2)
    assert probe.audit_batch_size == audit_batch_size


@pytest.mark.parametrize("audit_batch_size", [0, -1, 4097, True, False, 1.0, "1", None])
def test_database_readiness_rejects_invalid_audit_budget(
    tmp_path: Path, audit_batch_size: object
) -> None:
    with pytest.raises(ValueError, match="readiness audit batch size"):
        DatabaseReadinessProbe(
            cast(AsyncEngine, object()),
            tmp_path / "alembic.ini",
            audit_batch_size=cast(int, audit_batch_size),
        )


def test_database_readiness_default_and_helper_keep_original_audit_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def scenario() -> None:
        observed: list[int] = []

        async def check(
            *_: object, audit_batch_size: int, **__: object
        ) -> tuple[DatabaseReadiness, None]:
            observed.append(audit_batch_size)
            return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        engine = cast(AsyncEngine, object())
        config = tmp_path / "alembic.ini"
        probe = DatabaseReadinessProbe(engine, config)
        try:
            assert probe.audit_batch_size == 4096
            assert await probe.check() == DatabaseReadiness(True, "ready", 0)
            assert await check_database_readiness(engine, config) == DatabaseReadiness(
                True, "ready", 0
            )
            assert observed == [4096, 4096]
        finally:
            await probe.drain()

    asyncio.run(scenario())


def test_independent_database_readiness_probes_keep_distinct_audit_budgets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def scenario() -> None:
        observed: list[int] = []

        async def check(
            *_: object, audit_batch_size: int, **__: object
        ) -> tuple[DatabaseReadiness, None]:
            observed.append(audit_batch_size)
            return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probes = tuple(
            DatabaseReadinessProbe(
                cast(AsyncEngine, object()), tmp_path / "alembic.ini", audit_batch_size=budget
            )
            for budget in (1, 4)
        )
        try:
            for _ in range(2):
                for probe in probes:
                    assert await probe.check() == DatabaseReadiness(True, "ready", 0)
            assert observed == [1, 4, 1, 4]
        finally:
            for probe in probes:
                await probe.drain()

    asyncio.run(scenario())


def test_multiple_migration_heads_fail_before_database_access(tmp_path: Path) -> None:
    script_root = tmp_path / "alembic"
    versions = script_root / "versions"
    versions.mkdir(parents=True)
    (script_root / "script.py.mako").write_text("", encoding="utf-8")
    for revision in ("head_a", "head_b"):
        (versions / f"{revision}.py").write_text(
            f"revision = '{revision}'\n"
            "down_revision = None\n"
            "branch_labels = None\n"
            "depends_on = None\n",
            encoding="utf-8",
        )
    config = tmp_path / "alembic.ini"
    config.write_text(f"[alembic]\nscript_location = {script_root}\n", encoding="utf-8")

    result = asyncio.run(check_database_readiness(cast(AsyncEngine, object()), config))
    assert not result.ready
    assert result.reason == "code_migration_heads_not_unique"


def test_invalid_migration_metadata_is_explicitly_unavailable(tmp_path: Path) -> None:
    config = tmp_path / "alembic.ini"
    config.write_text("[alembic]\n", encoding="utf-8")

    result = asyncio.run(check_database_readiness(cast(AsyncEngine, object()), config))
    assert not result.ready
    assert result.reason == "code_migration_unavailable"


def test_concurrent_database_readiness_callers_share_one_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, tuple[DatabaseReadiness, ...]]:
        calls = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def check(*_: object, **__: object) -> tuple[DatabaseReadiness, None]:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(
            cast(AsyncEngine, object()),
            tmp_path / "alembic.ini",
            timeout_ms=1_000,
        )
        waiters = tuple(asyncio.create_task(probe.check()) for _ in range(8))
        await entered.wait()
        await asyncio.sleep(0)
        release.set()
        return calls, tuple(await asyncio.gather(*waiters))

    calls, results = asyncio.run(scenario())

    assert calls == 1
    assert results == (DatabaseReadiness(True, "ready", 0),) * 8


def test_cancelled_database_readiness_waiter_does_not_cancel_shared_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, DatabaseReadiness]:
        calls = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def check(*_: object, **__: object) -> tuple[DatabaseReadiness, None]:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(
            cast(AsyncEngine, object()),
            tmp_path / "alembic.ini",
            timeout_ms=1_000,
        )
        cancelled = asyncio.create_task(probe.check())
        surviving = asyncio.create_task(probe.check())
        await entered.wait()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release.set()
        return calls, await surviving

    assert asyncio.run(scenario()) == (1, DatabaseReadiness(True, "ready", 0))


def test_database_readiness_waiter_budget_rejects_without_starting_another_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[int, DatabaseReadiness]:
        calls = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def check(*_: object, **__: object) -> tuple[DatabaseReadiness, None]:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(
            cast(AsyncEngine, object()),
            tmp_path / "alembic.ini",
            timeout_ms=1_000,
        )
        waiters = []
        for _ in range(readiness_module.MAXIMUM_DATABASE_READINESS_WAITERS):
            waiters.append(asyncio.create_task(probe.check()))
            await asyncio.sleep(0)
        await entered.wait()
        rejected = await probe.check()
        release.set()
        await asyncio.gather(*waiters)
        return calls, rejected

    assert asyncio.run(scenario()) == (1, DatabaseReadiness(False, "readiness_overloaded"))


@pytest.mark.parametrize("candidate", ["ready", "audit_chain_invalid"])
def test_synchronous_deadline_overrun_cannot_advance_or_reset_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candidate: str,
) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        real_time = loop.time
        probe = DatabaseReadinessProbe(cast(AsyncEngine, object()), tmp_path / "alembic.ini")
        probe._verified = candidate == "audit_chain_invalid"
        previous_verified = probe._verified

        async def check(*_: object, **__: object) -> tuple[DatabaseReadiness, None]:
            monkeypatch.setattr(loop, "time", lambda: real_time() + 60)
            return DatabaseReadiness(candidate == "ready", candidate), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        try:
            result = await probe.check()
            assert result.reason == "readiness_timeout"
            assert probe._verified is previous_verified
            assert probe._last_record is None
        finally:
            monkeypatch.setattr(loop, "time", real_time)
            await probe.drain()

    asyncio.run(scenario())


@pytest.mark.parametrize("candidate", ["ready", "audit_chain_invalid"])
def test_stopped_worker_cannot_advance_or_reset_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candidate: str,
) -> None:
    async def scenario() -> None:
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0

        async def check(*_: object, **__: object) -> tuple[DatabaseReadiness, None]:
            nonlocal calls
            calls += 1
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return DatabaseReadiness(candidate == "ready", candidate), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(
            cast(AsyncEngine, object()), tmp_path / "alembic.ini", managed=True
        )
        assert (await probe.check()).reason == "readiness_stopped"
        assert calls == 0
        probe.activate()
        probe._verified = candidate == "audit_chain_invalid"
        previous_verified = probe._verified
        waiter = asyncio.create_task(probe.check())
        await entered.wait()
        probe.stop()
        await cancelled.wait()
        drains = [asyncio.create_task(probe.drain()) for _ in range(2)]
        await asyncio.sleep(0)
        assert all(not drain.done() for drain in drains)
        assert (await probe.check()).reason == "readiness_stopped"
        release.set()
        assert (await waiter).reason == "readiness_stopped"
        await asyncio.gather(*drains)
        assert calls == 1 and probe._verified is previous_verified
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            probe.activate()

    asyncio.run(scenario())


@pytest.mark.parametrize("cut", ["none", "waiter_cancel", "deadline", "stop"])
def test_retained_public_close_is_joined_before_prefix_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cut: str,
) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        class Connection:
            async def close(self) -> None:
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()

        class Engine:
            async def connect(self) -> AsyncConnection:
                return cast(AsyncConnection, Connection())

        async def check(
            connect: Callable[[], AbstractAsyncContextManager[AsyncConnection]],
            *_: object,
            **__: object,
        ) -> tuple[DatabaseReadiness, None]:
            async with connect():
                return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(
            cast(AsyncEngine, Engine()),
            tmp_path / "alembic.ini",
            timeout_ms=50 if cut == "deadline" else 1_000,
        )
        waiter = asyncio.create_task(probe.check())
        await entered.wait()
        worker, finalizer = probe._inflight, probe._connection_finalizer
        assert worker is not None and finalizer is not None
        if cut == "waiter_cancel":
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        elif cut == "deadline":
            assert (await waiter).reason == "readiness_timeout"
        elif cut == "stop":
            probe.stop()
        await asyncio.sleep(0)
        assert not worker.done() and not finalizer.done()
        assert not probe._verified
        if cut != "stop":
            joined = asyncio.create_task(probe.check())
            await asyncio.sleep(0)
            assert probe._inflight is worker
        release.set()
        await asyncio.gather(worker, waiter, return_exceptions=True)
        if cut != "stop":
            await joined
        assert calls == 1 and finalizer.done()
        assert probe._verified is (cut in {"none", "waiter_cancel"})
        await probe.drain()

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_close", [False, True])
def test_query_failure_is_retryable_only_after_successful_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_close: bool,
) -> None:
    async def scenario() -> None:
        connects = closes = 0

        class Connection:
            async def close(self) -> None:
                nonlocal closes
                closes += 1
                if failed_close:
                    raise SQLAlchemyError("private close canary")

        class Engine:
            async def connect(self) -> AsyncConnection:
                nonlocal connects
                connects += 1
                return cast(AsyncConnection, Connection())

        async def check(
            connect: Callable[[], AbstractAsyncContextManager[AsyncConnection]],
            *_: object,
            **__: object,
        ) -> tuple[DatabaseReadiness, None]:
            try:
                async with connect():
                    raise SQLAlchemyError("private query canary")
            except SQLAlchemyError:
                return DatabaseReadiness(False, "store_unavailable_or_invalid"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(cast(AsyncEngine, Engine()), tmp_path / "alembic.ini")
        first = await probe.check()
        assert await probe.check() == first
        assert connects == closes == (1 if failed_close else 2)
        assert "private" not in repr(first)
        for _ in range(2):
            if failed_close:
                with pytest.raises(RuntimeError, match="database readiness cleanup failed"):
                    await probe.drain()
            else:
                await probe.drain()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("body", "cancel_during_cleanup"),
    [("query", False), ("cancel", False), ("none", False), ("query", True), ("cancel", True)],
)
@pytest.mark.parametrize("close_outcome", ["success", "error", "cancel"])
def test_connection_close_preserves_the_exact_escaping_primary(
    tmp_path: Path,
    body: str,
    close_outcome: str,
    cancel_during_cleanup: bool,
) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        primary = (
            SQLAlchemyError("query primary")
            if body == "query"
            else asyncio.CancelledError("body primary")
            if body == "cancel"
            else None
        )
        close_failure = SQLAlchemyError("private-close-canary")

        class Connection:
            async def close(self) -> None:
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                if close_outcome == "error":
                    raise close_failure
                if close_outcome == "cancel":
                    raise asyncio.CancelledError("private-close-canary")

        class Engine:
            async def connect(self) -> AsyncConnection:
                return cast(AsyncConnection, Connection())

        probe = DatabaseReadinessProbe(cast(AsyncEngine, Engine()), tmp_path / "alembic.ini")

        async def use_connection() -> BaseException | None:
            try:
                async with probe._connect():
                    if primary is not None:
                        raise primary
            except BaseException as error:
                return error
            return None

        operation = asyncio.create_task(use_connection())
        try:
            await entered.wait()
            assert not operation.done() and calls == 1
            if cancel_during_cleanup:
                assert operation.cancel("later caller cancellation")
                await asyncio.sleep(0)
                assert not operation.done()
            release.set()
            escaped = await operation
            if primary is not None:
                assert escaped is primary
            elif close_outcome == "success":
                assert escaped is None
            else:
                assert isinstance(escaped, SQLAlchemyError)
                assert str(escaped) == "readiness connection cleanup failed"
            if escaped is not None:
                assert "private-close-canary" not in "".join(traceback.format_exception(escaped))
                assert getattr(escaped, "__notes__", []) == (
                    ["database readiness connection cleanup also failed"]
                    if primary is not None and close_outcome != "success"
                    else []
                ) + (
                    ["additional cancellation during database readiness cleanup"]
                    if cancel_during_cleanup
                    else []
                )
            assert probe._close_failed is (close_outcome != "success")
            assert probe._connection_finalizer is not None
            assert probe._connection_finalizer.done()
            for _ in range(2):
                if close_outcome == "success":
                    await probe.drain()
                else:
                    with pytest.raises(RuntimeError, match="database readiness cleanup failed"):
                        await probe.drain()
            assert calls == 1
        finally:
            release.set()
            await operation

    asyncio.run(scenario())


def test_ambient_handled_exception_does_not_hide_cleanup_only_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        calls = 0
        failure = SQLAlchemyError("private-close-canary")

        class Connection:
            async def close(self) -> None:
                nonlocal calls
                calls += 1
                raise failure

        class Engine:
            async def connect(self) -> AsyncConnection:
                return cast(AsyncConnection, Connection())

        probe = DatabaseReadinessProbe(cast(AsyncEngine, Engine()), tmp_path / "alembic.ini")
        try:
            raise LookupError("already handled")
        except LookupError:
            with pytest.raises(
                SQLAlchemyError, match="readiness connection cleanup failed"
            ) as captured:
                async with probe._connect():
                    pass
        assert "private-close-canary" not in "".join(traceback.format_exception(captured.value))
        assert calls == 1 and probe._close_failed
        with pytest.raises(RuntimeError, match="database readiness cleanup failed"):
            await probe.drain()

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_close", [False, True])
def test_database_drain_preserves_remembered_caller_cancellation_before_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_close: bool,
) -> None:
    async def scenario() -> None:
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = 0
        observed: list[asyncio.CancelledError] = []
        draining: asyncio.Task[None] | None = None
        original_wait = asyncio.wait

        async def recording_wait(
            tasks: set[asyncio.Task[object]],
        ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
            try:
                return await original_wait(tasks)
            except asyncio.CancelledError as error:
                if asyncio.current_task() is draining:
                    observed.append(error)
                    cancelled.set()
                raise

        class Connection:
            async def close(self) -> None:
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                if failed_close:
                    raise SQLAlchemyError("private-close-canary")

        class Engine:
            async def connect(self) -> AsyncConnection:
                return cast(AsyncConnection, Connection())

        async def check(
            connect: Callable[[], AbstractAsyncContextManager[AsyncConnection]],
            *_: object,
            **__: object,
        ) -> tuple[DatabaseReadiness, None]:
            async with connect():
                return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        monkeypatch.setattr(asyncio, "wait", recording_wait)
        probe = DatabaseReadinessProbe(cast(AsyncEngine, Engine()), tmp_path / "alembic.ini")
        waiter = asyncio.create_task(probe.check())
        try:
            await entered.wait()
            draining = asyncio.create_task(probe.drain())
            await asyncio.sleep(0)
            assert draining.cancel("drain caller")
            async with asyncio.timeout(1):
                await cancelled.wait()
            assert not draining.done() and len(observed) == 1
            assert (
                probe._connection_finalizer is not None and not probe._connection_finalizer.done()
            )
            release.set()
            with pytest.raises(asyncio.CancelledError) as captured:
                await draining
            assert captured.value is observed[0]
            assert getattr(captured.value, "__notes__", []) == (
                ["database readiness cleanup also failed"] if failed_close else []
            )
            assert "private-close-canary" not in "".join(traceback.format_exception(captured.value))
            assert calls == 1 and probe._close_failed is failed_close
            assert probe._inflight is not None and probe._inflight.done()
            if failed_close:
                with pytest.raises(RuntimeError, match="database readiness cleanup failed"):
                    await probe.drain()
            else:
                await probe.drain()
            assert calls == 1
        finally:
            release.set()
            await asyncio.gather(
                waiter, *(() if draining is None else (draining,)), return_exceptions=True
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_caller", [False, True])
@pytest.mark.parametrize("cancel_child", [False, True])
def test_database_drain_distinguishes_caller_cancellation_when_worker_is_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cancel_caller: bool,
    cancel_child: bool,
) -> None:
    async def scenario() -> None:
        entered, stopped, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        observed: list[asyncio.CancelledError] = []
        completion_cancellations: list[bool] = []
        draining: asyncio.Task[None] | None = None
        original_wait = asyncio.wait

        async def check(*_: object, **__: object) -> tuple[DatabaseReadiness, None]:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                stopped.set()
                await release.wait()
            if cancel_child:
                raise asyncio.CancelledError("owned child")
            return DatabaseReadiness(True, "ready"), None

        monkeypatch.setattr(readiness_module, "_check_database_readiness", check)
        probe = DatabaseReadinessProbe(cast(AsyncEngine, object()), tmp_path / "alembic.ini")
        waiter = asyncio.create_task(probe.check())
        await entered.wait()
        worker = probe._inflight
        assert worker is not None

        async def recording_wait(
            tasks: set[asyncio.Task[object]],
        ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
            try:
                return await original_wait(tasks)
            except asyncio.CancelledError as error:
                if asyncio.current_task() is draining:
                    assert worker.done()
                    observed.append(error)
                raise

        def on_done(_task: object) -> None:
            assert draining is not None
            if cancel_caller:
                completion_cancellations.append(draining.cancel("drain caller"))

        monkeypatch.setattr(asyncio, "wait", recording_wait)
        try:
            worker.add_done_callback(on_done)
            draining = asyncio.create_task(probe.drain())
            await stopped.wait()
            assert not draining.done() and not worker.done()
            release.set()
            if cancel_caller:
                with pytest.raises(asyncio.CancelledError) as captured:
                    await draining
                assert observed == [captured.value]
                assert captured.value is observed[0]
                assert completion_cancellations == [True]
            else:
                await draining
                assert observed == []
                assert completion_cancellations == []
            assert worker.done() and worker.cancelled() is cancel_child
            await probe.drain()
        finally:
            release.set()
            await asyncio.gather(
                waiter, *(() if draining is None else (draining,)), return_exceptions=True
            )

    asyncio.run(scenario())
