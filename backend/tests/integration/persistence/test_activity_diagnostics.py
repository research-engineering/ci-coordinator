import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal

import psycopg
import pytest
from control_plane_http_support import human_principal
from sqlalchemy import func, insert, literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.persistence import activity_repository, activity_write
from ci_coordinator.persistence._schema_activity import activity_diagnostic_buckets, activity_events
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("action,outcome", [("role_denied", "denied"), ("export", "attempted")])
@pytest.mark.parametrize(
    "before,after,event_count",
    [(126, 127, 1), (127, 128, 1), (128, 129, 0), (999999, 1000000, 0), (1000000, 1000000, 0)],
)
def test_principal_diagnostic_quota_and_saturation_preserve_exact_events(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    action: Literal["role_denied", "export"],
    outcome: str,
    before: int,
    after: int,
    event_count: int,
) -> None:
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        clock = connection.execute("SELECT statement_timestamp()").fetchone()
        assert clock is not None and isinstance(clock[0], datetime)
        now = clock[0].astimezone(UTC)
        bucket = now.replace(minute=0, second=0, microsecond=0)
        connection.execute(
            "INSERT INTO ci_coordinator.activity_diagnostic_buckets (bucket, action, count) "
            "VALUES (%s, %s, %s)",
            (bucket, action, before),
        )
    # Freeze only SQL time to keep the prepared bucket valid across an hour rollover.
    # noinspection PyUnresolvedReferences
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
    principal = replace(human_principal(), expires_at=now + timedelta(minutes=5))

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            await store.principal_diagnostic(principal, action)
            async with engine.connect() as connection:
                buckets = (
                    (await connection.execute(select(activity_diagnostic_buckets))).mappings().all()
                )
                assert [(row["bucket"], row["action"], row["count"]) for row in buckets] == [
                    (bucket, action, after)
                ]
                rows = (await connection.execute(select(activity_events))).mappings().all()
                assert len(rows) == event_count
                assert [
                    (row["issuer"], row["subject"], row["actor"], row["action"], row["outcome"])
                    for row in rows
                ] == [
                    (principal.issuer, principal.subject, principal.actor_id, action, outcome)
                ] * event_count
                assert all(row["occurred_at"] == now for row in rows)
                assert all(row["retain_until"] == now + timedelta(days=30) for row in rows)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("before", [999999, 1000000])
def test_anonymous_cleanup_retains_its_cutoff_bound_and_counter_saturation(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    before: int,
) -> None:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    _freeze_clock(monkeypatch, now)
    expired = sorted(now - timedelta(hours=48 + index) for index in range(200))
    retained = now - timedelta(hours=47)
    principal = human_principal()

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            async with admin.begin() as seed:
                await seed.execute(
                    insert(activity_diagnostic_buckets),
                    [
                        {"bucket": bucket, "action": "login_rejected", "count": 7}
                        for bucket in (*expired, retained)
                    ]
                    + [{"bucket": now, "action": "login_rejected", "count": before}],
                )
                await seed.execute(
                    insert(activity_events).values(
                        occurred_at=now - timedelta(days=31),
                        retain_until=now - timedelta(days=1),
                        issuer=principal.issuer,
                        subject=principal.subject,
                        actor=principal.actor_id,
                        action="login",
                        outcome="committed",
                        operation_ref="00000000-0000-0000-0000-000000000001",
                    )
                )
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            await store.login_diagnostic("login_rejected")
            async with engine.connect() as connection:
                remaining = list(
                    await connection.scalars(
                        select(activity_diagnostic_buckets.c.bucket)
                        .where(activity_diagnostic_buckets.c.bucket < now)
                        .order_by(activity_diagnostic_buckets.c.bucket)
                    )
                )
                assert remaining == [*expired[128:], retained]
                assert (
                    await connection.scalar(
                        select(activity_diagnostic_buckets.c.count).where(
                            activity_diagnostic_buckets.c.bucket == now
                        )
                    )
                    == 1000000
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 1
                )
            await store.login_diagnostic("login_rejected")
            assert await store.cleanup() == 1
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        select(
                            activity_diagnostic_buckets.c.bucket,
                            activity_diagnostic_buckets.c.count,
                        ).order_by(activity_diagnostic_buckets.c.bucket)
                    )
                ).all()
                assert [tuple(row) for row in rows] == [(retained, 7), (now, 1000000)]
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_anonymous_counter_failure_rolls_back_bucket_cleanup(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    cutoff = now - timedelta(hours=48)
    _freeze_clock(monkeypatch, now)
    original = activity_write.record_diagnostic_count

    async def interrupted(connection: AsyncConnection, action: str) -> int:
        assert await original(connection, action) == 42
        if cancelled:
            raise asyncio.CancelledError()
        raise SQLAlchemyError("counter transaction interrupted")

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            async with admin.begin() as seed:
                await seed.execute(
                    insert(activity_diagnostic_buckets),
                    [
                        {"bucket": cutoff, "action": "login_unavailable", "count": 3},
                        {"bucket": now, "action": "login_unavailable", "count": 41},
                    ],
                )
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            with monkeypatch.context() as patch:
                patch.setattr(activity_repository, "record_diagnostic_count", interrupted)
                with pytest.raises(asyncio.CancelledError if cancelled else ActivityUnavailable):
                    await store.login_diagnostic("login_unavailable")
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        select(
                            activity_diagnostic_buckets.c.bucket,
                            activity_diagnostic_buckets.c.count,
                        ).order_by(activity_diagnostic_buckets.c.bucket)
                    )
                ).all()
                assert [tuple(row) for row in rows] == [(cutoff, 3), (now, 41)]
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 0
                )
            await store.login_diagnostic("login_unavailable")
            async with engine.connect() as connection:
                retained_rows = (
                    (await connection.execute(select(activity_diagnostic_buckets))).mappings().all()
                )
                assert [(row["bucket"], row["count"]) for row in retained_rows] == [(now, 42)]
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_overlapping_anonymous_cleanup_preserves_retained_counts(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    _freeze_clock(monkeypatch, now)
    original_cleanup = activity_write.cleanup_diagnostic_buckets
    original_counter = activity_write.record_diagnostic_count

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        arrived = asyncio.Barrier(2)
        counts: list[int] = []

        async def concurrent_cleanup(connection: AsyncConnection) -> None:
            await arrived.wait()
            await original_cleanup(connection)

        async def observed_counter(connection: AsyncConnection, action: str) -> int:
            value = await original_counter(connection, action)
            counts.append(value)
            return value

        try:
            async with admin.begin() as seed:
                await seed.execute(
                    insert(activity_diagnostic_buckets),
                    [
                        {
                            "bucket": now - timedelta(hours=48),
                            "action": "login_rejected",
                            "count": 5,
                        },
                        {"bucket": now, "action": "login_rejected", "count": 999998},
                    ],
                )
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            with monkeypatch.context() as patch:
                patch.setattr(activity_repository, "cleanup_diagnostic_buckets", concurrent_cleanup)
                patch.setattr(activity_repository, "record_diagnostic_count", observed_counter)
                async with asyncio.timeout(10), asyncio.TaskGroup() as group:
                    group.create_task(store.login_diagnostic("login_rejected"))
                    group.create_task(store.login_diagnostic("login_rejected"))
            assert sorted(counts) == [999999, 1000000]
            await store.login_diagnostic("login_rejected")
            async with engine.connect() as connection:
                rows = (
                    (await connection.execute(select(activity_diagnostic_buckets))).mappings().all()
                )
                assert [(row["bucket"], row["count"]) for row in rows] == [(now, 1000000)]
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 0
                )
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
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
