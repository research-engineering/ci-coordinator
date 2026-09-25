import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Literal

import pytest
from control_plane_http_support import human_principal
from sqlalchemy import func, insert, literal, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutApplied,
    BackChannelLogoutEvidence,
    BackChannelLogoutStoreUnavailable,
    BackChannelLogoutTarget,
    ControlPlaneSessionRecord,
    DisplayMetadata,
)
from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.persistence import activity_write
from ci_coordinator.persistence._schema_activity import activity_diagnostic_buckets, activity_events
from ci_coordinator.persistence._schema_control_plane_identity import control_plane_logout_replays
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.control_plane_session_repository import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
)

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("action", ["role_denied", "export"])
def test_held_journal_mutex_spares_anonymous_but_not_ordered_writers(
    runtime_postgres_database_url: str, action: Literal["role_denied", "export"]
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            record = await _session(sessions)
            logouts = PostgresBackChannelLogoutStore(engine)
            evidence = _logout(record)
            activity = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            principal = replace(human_principal(), expires_at=record.expires_at)
            async with asyncio.timeout(10), engine.begin() as holder:
                await activity_write.lock_activity(holder)
                await activity.login_diagnostic("login_rejected")
                with pytest.raises(ActivityUnavailable) as diagnostic:
                    await activity.principal_diagnostic(principal, action)
                _assert_lock_timeout(diagnostic.value)
                with pytest.raises(BackChannelLogoutStoreUnavailable) as revocation:
                    await logouts.consume_and_delete(
                        evidence=evidence, replay_retained_until=evidence.expires_at
                    )
                _assert_lock_timeout(revocation.value)
                assert await sessions.load(record.handle_digest) == record
                assert await holder.scalar(select(func.count()).select_from(activity_events)) == 1
                assert (
                    await holder.scalar(
                        select(func.count()).select_from(control_plane_logout_replays)
                    )
                    == 0
                )
                assert await holder.scalar(select(activity_diagnostic_buckets.c.count)) == 1
            await activity.principal_diagnostic(principal, action)
            applied = await logouts.consume_and_delete(
                evidence=evidence, replay_retained_until=evidence.expires_at
            )
            assert isinstance(applied, BackChannelLogoutApplied)
            assert applied.deleted_session_count == 1
            assert await sessions.load(record.handle_digest) is None
            async with engine.connect() as connection:
                assert list(
                    await connection.scalars(
                        select(activity_events.c.action).order_by(activity_events.c.sequence)
                    )
                ) == ["login", action, "revoked"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_counter_statements_preserve_both_increments_after_row_contention(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = datetime(2026, 9, 25, 12, tzinfo=UTC)
            monkeypatch.setattr(
                activity_write,
                "func",
                SimpleNamespace(
                    statement_timestamp=lambda: literal(now),
                    date_trunc=func.date_trunc,
                    least=func.least,
                    count=func.count,
                ),
            )
            async with admin.begin() as seed:
                await seed.execute(
                    insert(activity_diagnostic_buckets).values(
                        bucket=now, action="login_rejected", count=999998
                    )
                )

            async def increment(entered: asyncio.Future[int]) -> int:
                async with engine.begin() as connection:
                    pid = await connection.scalar(select(func.pg_backend_pid()))
                    assert type(pid) is int
                    entered.set_result(pid)
                    return await activity_write.record_diagnostic_count(
                        connection, "login_rejected"
                    )

            # Exercise the SQL primitive, not the store's shorter admission budget.
            async with asyncio.timeout(10), admin.connect() as observer:
                async with asyncio.TaskGroup() as group:
                    async with admin.begin() as holder:
                        await holder.execute(select(activity_diagnostic_buckets).with_for_update())
                        holder_pid = await holder.scalar(select(func.pg_backend_pid()))
                        assert type(holder_pid) is int
                        entered: list[asyncio.Future[int]] = [
                            asyncio.get_running_loop().create_future() for _ in range(2)
                        ]
                        tasks = [group.create_task(increment(signal)) for signal in entered]
                        first, second = await asyncio.gather(*entered)
                        await _observe_counter_waiters(observer, first, second, holder_pid)
                    counts = await asyncio.gather(*tasks)
                assert sorted(counts) == [999999, 1000000]
                assert await observer.scalar(select(activity_diagnostic_buckets.c.count)) == 1000000
                assert await observer.scalar(select(func.count()).select_from(activity_events)) == 0
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


async def _observe_counter_waiters(
    observer: AsyncConnection, first_pid: int, second_pid: int, holder_pid: int
) -> None:
    while True:
        first, second = (
            await observer.execute(
                text("SELECT pg_blocking_pids(:first), pg_blocking_pids(:second)"),
                {"first": first_pid, "second": second_pid},
            )
        ).one()
        if (holder_pid in first and (holder_pid in second or first_pid in second)) or (
            holder_pid in second and (holder_pid in first or second_pid in first)
        ):
            return


async def _session(store: PostgresControlPlaneSessionStore) -> ControlPlaneSessionRecord:
    now = await store.current_time()
    principal = human_principal()
    record = ControlPlaneSessionRecord(
        handle_digest=sha256(b"diagnostic-isolation-session").digest(),
        issuer=principal.issuer,
        subject=principal.subject,
        keycloak_session_id="diagnostic-isolation-session",
        actor_id=principal.actor_id,
        roles=principal.roles,
        authority_profile_digest="a" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        display=DisplayMetadata(None, None),
    )
    await store.replace(previous_handle_digest=None, record=record)
    return record


def _logout(record: ControlPlaneSessionRecord) -> BackChannelLogoutEvidence:
    return BackChannelLogoutEvidence(
        issuer=record.issuer,
        token_id="diagnostic-isolation-logout",
        issued_at=record.issued_at,
        expires_at=record.issued_at + timedelta(minutes=1),
        target=BackChannelLogoutTarget(subject=record.subject),
    )


def _assert_lock_timeout(error: Exception) -> None:
    assert isinstance(error.__cause__, DBAPIError)
    assert getattr(error.__cause__.orig, "sqlstate", None) == "55P03"
