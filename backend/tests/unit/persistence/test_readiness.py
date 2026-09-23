from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

import ci_coordinator.persistence.readiness as readiness_module
from ci_coordinator.persistence import (
    DatabaseReadiness,
    DatabaseReadinessProbe,
    check_database_readiness,
)


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
