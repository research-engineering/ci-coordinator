import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal

import psycopg
import pytest
from control_plane_http_support import human_principal
from sqlalchemy import func, literal, select

from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.persistence import activity_write
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
