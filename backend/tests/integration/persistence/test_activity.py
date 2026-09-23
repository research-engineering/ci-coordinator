import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

import psycopg
import pytest
from control_plane_http_support import human_principal
from sqlalchemy import func, literal, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.dml import Delete

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutEvidence,
    BackChannelLogoutReplay,
    BackChannelLogoutTarget,
    ControlPlaneSessionRecord,
    ControlPlaneSessionStoreUnavailable,
    DisplayMetadata,
)
from ci_coordinator.control_plane_identity.activity import ActivityUnavailable, SessionEndReason
from ci_coordinator.control_plane_identity.activity_cursor import (
    ActivityCursorCodec,
    ActivityPosition,
)
from ci_coordinator.control_plane_identity.activity_query import ActivityQuery
from ci_coordinator.persistence import activity_repository, activity_write
from ci_coordinator.persistence import control_plane_session_repository as session_owner
from ci_coordinator.persistence._schema_activity import activity_events
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.control_plane_session_repository import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
)
from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string

pytestmark = pytest.mark.persistence


@pytest.fixture(autouse=True)
def clean_activity(postgres_database_url: str, clean_migrated_database: None) -> Iterator[None]:
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        connection.execute(
            "TRUNCATE ci_coordinator.activity_events, ci_coordinator.activity_diagnostic_buckets"
        )
    yield


async def _record(
    store: PostgresControlPlaneSessionStore, key: str = "session"
) -> ControlPlaneSessionRecord:
    now = await store.current_time()
    principal = human_principal()
    return ControlPlaneSessionRecord(
        handle_digest=sha256(key.encode()).digest(),
        issuer=principal.issuer,
        subject=principal.subject,
        keycloak_session_id="not-for-journal",
        actor_id=principal.actor_id,
        roles=principal.roles,
        authority_profile_digest="a" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        display=DisplayMetadata("secret-display", None),
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_session_and_journal_rollback_together_on_failure_or_cancellation(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    original = activity_write.record_session_login

    async def fail(connection: AsyncConnection, record: ControlPlaneSessionRecord) -> None:
        await original(connection, record)
        if cancelled:
            raise asyncio.CancelledError
        raise SQLAlchemyError("credential-marker")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(session_owner, "record_session_login", fail)

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            record = await _record(store)
            with pytest.raises(
                asyncio.CancelledError if cancelled else ControlPlaneSessionStoreUnavailable
            ):
                await store.replace(previous_handle_digest=None, record=record)
            assert await store.load(record.handle_digest) is None
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_login_logout_replay_and_backchannel_are_actual_effects(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            record = await _record(store)
            await store.replace(previous_handle_digest=None, record=record)
            await store.delete(record.handle_digest)
            await store.delete(record.handle_digest)
            second = await _record(store, "second")
            await store.replace(previous_handle_digest=None, record=second)
            evidence = BackChannelLogoutEvidence(
                issuer=second.issuer,
                token_id="replay-identifier",
                issued_at=second.issued_at,
                expires_at=second.issued_at + timedelta(seconds=60),
                target=BackChannelLogoutTarget(subject=second.subject),
            )
            logouts = PostgresBackChannelLogoutStore(engine)
            await logouts.consume_and_delete(
                evidence=evidence, replay_retained_until=evidence.expires_at
            )
            assert isinstance(
                await logouts.consume_and_delete(
                    evidence=evidence, replay_retained_until=evidence.expires_at
                ),
                BackChannelLogoutReplay,
            )
            async with engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(activity_events).order_by(activity_events.c.sequence)
                        )
                    )
                    .mappings()
                    .all()
                )
            assert [row["action"] for row in rows] == ["login", "logout", "login", "revoked"]
            assert all(row["outcome"] == "committed" for row in rows)
            assert all(row["actor"] == record.actor_id for row in rows)
            assert "secret-display" not in repr(rows)
            assert "not-for-journal" not in repr(rows)
            assert "replay-identifier" not in repr(rows)
            assert set(rows[0]) == {
                "sequence",
                "occurred_at",
                "retain_until",
                "issuer",
                "subject",
                "actor",
                "action",
                "outcome",
                "operation_ref",
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_expiry_retains_actor_once_and_logout_failure_rolls_back(
    runtime_postgres_database_url: str, postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            record = await _record(store)
            await store.replace(previous_handle_digest=None, record=record)
            original = activity_write.delete_sessions_with_activity

            async def fail(
                connection: AsyncConnection, statement: Delete, action: SessionEndReason
            ) -> int:
                await original(connection, statement, action)
                raise SQLAlchemyError("journal unavailable")

            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(session_owner, "delete_sessions_with_activity", fail)
                with pytest.raises(ControlPlaneSessionStoreUnavailable):
                    await store.delete(record.handle_digest)
            assert session_owner.__dict__["delete_sessions_with_activity"] is original
            assert await store.load(record.handle_digest) == record
            with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as admin:
                admin.execute(
                    "WITH prior AS (DELETE FROM ci_coordinator.control_plane_sessions "
                    "WHERE handle_digest = %s RETURNING *) "
                    "INSERT INTO ci_coordinator.control_plane_sessions "
                    "(handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
                    "profile_digest, issued_at, expires_at, preferred_username, display_name) "
                    "SELECT handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
                    "profile_digest, statement_timestamp() - interval '6 minutes', "
                    "statement_timestamp() - interval '1 minute', preferred_username, "
                    "display_name FROM prior",
                    (record.handle_digest,),
                )
            assert await store.load(record.handle_digest) is None
            assert await store.load(record.handle_digest) is None
            async with engine.connect() as connection:
                actions = (
                    (
                        await connection.execute(
                            select(activity_events.c.action).order_by(activity_events.c.sequence)
                        )
                    )
                    .scalars()
                    .all()
                )
            assert actions == ["login", "expired"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_keyset_projection_survives_reconnect_and_excludes_newer_events(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            record = await _record(sessions, "one")
            await sessions.replace(previous_handle_digest=None, record=record)
            second = await _record(sessions, "two")
            await sessions.replace(previous_handle_digest=None, record=second)
            query = ActivityQuery(
                "security",
                (record.issued_at - timedelta(seconds=1)).replace(microsecond=0),
                (record.issued_at + timedelta(minutes=1)).replace(microsecond=0),
                issuer=record.issuer,
                action="login",
                limit=1,
            )
            principal = replace(human_principal(), expires_at=record.expires_at)
            reader = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            first = await reader.page(query, principal, None)
            assert len(first.items) == 1 and first.next_cursor is not None
            third = await _record(sessions, "three")
            await sessions.replace(previous_handle_digest=None, record=third)
            # A fresh reader has no process-local pagination or persistence authority.
            reader = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            last = await reader.page(query, principal, first.next_cursor)
            assert len(last.items) == 1 and last.next_cursor is None
            assert last.items[0].sequence < first.items[0].sequence
            assert last.items[0].actor == principal.actor_id
            foreign = replace(query, issuer="https://other.example/realm")
            assert (await reader.page(foreign, principal, None)).items == ()
            with pytest.raises(ValueError):
                await reader.page(foreign, principal, first.next_cursor)
            with pytest.raises(ValueError):
                await reader.page(
                    query, replace(principal, authority_profile_digest="b" * 64), first.next_cursor
                )
            stale = reader._codec.encode(
                query,
                principal,
                ActivityPosition(
                    first.items[0].sequence,
                    last.items[0].sequence,
                    record.issued_at - timedelta(seconds=1),
                ),
            )
            with pytest.raises(ValueError):
                await reader.page(query, principal, stale)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_anonymous_failures_are_fixed_buckets_not_credential_rows(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            for _ in range(10):
                await store.login_diagnostic("login_rejected")
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            "SELECT bucket, action, count "
                            "FROM ci_coordinator.activity_diagnostic_buckets"
                        )
                    )
                ).all()
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 0
                )
            assert len(rows) in (1, 2)
            assert {row[1] for row in rows} == {"login_rejected"}
            assert sum(row[2] for row in rows) == 10
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_retention_hides_expired_records_before_bounded_erasure(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    principal = human_principal()
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        connection.execute(
            "INSERT INTO ci_coordinator.activity_events "
            "(occurred_at, retain_until, issuer, subject, actor, action, outcome, operation_ref) "
            "SELECT statement_timestamp() - interval '2592300 seconds', "
            "statement_timestamp() - interval '300 seconds', %s, %s, %s, 'login', 'committed', "
            "'00000000-0000-0000-0000-000000000001' FROM generate_series(1, 200)",
            (principal.issuer, principal.subject, principal.actor_id),
        )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            now = (await sessions.current_time()).replace(microsecond=0)
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"k" * 32))
            current = replace(principal, expires_at=now + timedelta(minutes=5))
            query = ActivityQuery(
                "security", now - timedelta(days=31), now, issuer=principal.issuer
            )
            assert (await store.page(query, current, None)).items == ()
            assert await store.cleanup() == 128
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 72
                )
            assert (await store.page(query, current, None)).items == ()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_logout_and_replacement_journal_only_actual_deletions(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            previous = await _record(store, "previous")
            successor = await _record(store, "successor")
            await store.replace(previous_handle_digest=None, record=previous)
            outcomes = await asyncio.gather(
                store.delete(previous.handle_digest),
                store.replace(previous_handle_digest=previous.handle_digest, record=successor),
                return_exceptions=True,
            )
            assert all(
                value is None or isinstance(value, ControlPlaneSessionStoreUnavailable)
                for value in outcomes
            )
            old = await store.load(previous.handle_digest)
            new = await store.load(successor.handle_digest)
            async with engine.connect() as connection:
                actions = list(
                    (await connection.execute(select(activity_events.c.action))).scalars()
                )
            assert actions.count("login") == 1 + int(new is not None)
            assert actions.count("logout") + actions.count("replaced") == int(old is None)
            if outcomes[0] is None or outcomes[1] is None:
                assert old is None
            if outcomes[1] is None:
                assert new == successor
            if new is None:
                await store.replace(previous_handle_digest=previous.handle_digest, record=successor)
            assert await store.load(previous.handle_digest) is None
            assert await store.load(successor.handle_digest) == successor
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_interrupted_cleanup_rolls_back_all_deletions(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    principal = human_principal()
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        connection.execute(
            "INSERT INTO ci_coordinator.activity_events "
            "(occurred_at, retain_until, issuer, subject, actor, action, outcome, operation_ref) "
            "VALUES (statement_timestamp() - interval '31 days', "
            "statement_timestamp() - interval '1 day', %s, %s, %s, 'login', 'committed', "
            "'00000000-0000-0000-0000-000000000001')",
            (principal.issuer, principal.subject, principal.actor_id),
        )
    original = activity_write.cleanup_activity

    async def interrupted(connection: AsyncConnection) -> int:
        assert await original(connection) == 1
        if cancelled:
            raise asyncio.CancelledError()
        raise SQLAlchemyError("injected cleanup interruption")

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            reader = PostgresActivityStore(engine, ActivityCursorCodec(b"a" * 32))
            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(activity_repository, "cleanup_activity", interrupted)
                with pytest.raises(asyncio.CancelledError if cancelled else ActivityUnavailable):
                    await reader.cleanup()
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 1
                )
            assert await reader.cleanup() == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_exact_retention_boundary_uses_the_same_half_open_predicate(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = human_principal()
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        row = connection.execute(
            "SELECT date_trunc('milliseconds', statement_timestamp())"
        ).fetchone()
        assert row is not None
        now = row[0]
        assert isinstance(now, datetime)
        connection.execute(
            "INSERT INTO ci_coordinator.activity_events "
            "(occurred_at, retain_until, issuer, subject, actor, action, outcome, operation_ref) "
            "SELECT %s + delta * interval '1 millisecond' - interval '2592000 seconds', "
            "%s + delta * interval '1 millisecond', %s, %s, %s, 'login', 'committed', "
            "'00000000-0000-0000-0000-000000000001' FROM generate_series(-1, 1) delta",
            (now, now, principal.issuer, principal.subject, principal.actor_id),
        )

    async def frozen_time(_connection: AsyncConnection) -> datetime:
        return now

    monkeypatch.setattr(activity_repository, "_database_time", frozen_time)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        activity_write,
        "func",
        SimpleNamespace(
            statement_timestamp=lambda: literal(now),
            count=func.count,
        ),
    )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresActivityStore(engine, ActivityCursorCodec(b"a" * 32))
            current = replace(principal, expires_at=now + timedelta(minutes=5))
            query = ActivityQuery(
                "security", now - timedelta(days=31), now, issuer=principal.issuer
            )
            page = await store.page(query, current, None)
            assert len(page.items) == 1
            assert page.items[0].occurred_at + timedelta(days=30) == now + timedelta(milliseconds=1)
            assert await store.cleanup() == 2
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
