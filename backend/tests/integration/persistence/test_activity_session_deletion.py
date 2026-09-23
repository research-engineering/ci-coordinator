import asyncio
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncResult
from sqlalchemy.sql import Executable

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutApplied,
    BackChannelLogoutEvidence,
    BackChannelLogoutReplay,
    BackChannelLogoutStoreUnavailable,
    BackChannelLogoutTarget,
    ControlPlaneSessionRecord,
    DisplayMetadata,
    derive_human_actor_id,
)
from ci_coordinator.control_plane_identity.activity import SessionEndReason
from ci_coordinator.persistence._schema_activity import activity_events
from ci_coordinator.persistence._schema_control_plane_identity import (
    control_plane_logout_replays,
    control_plane_sessions,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.control_plane_session_repository import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
)
from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string

pytestmark = pytest.mark.persistence

_ISSUER = "https://identity.example/realms/control-plane"
_SID = "retained-session-not-for-journal"
_FOREIGN_ACTOR = derive_human_actor_id(_ISSUER, "not-the-retained-subject")


def _seed_sessions(
    database_url: str,
    count: int,
    *,
    expired: bool = False,
    same_subject: bool = False,
) -> list[ControlPlaneSessionRecord]:
    with psycopg.connect(to_psycopg_connection_string(database_url)) as connection:
        clock = connection.execute("SELECT statement_timestamp()").fetchone()
        assert clock is not None and isinstance(clock[0], datetime)
        issued_at = clock[0] - timedelta(minutes=10 if expired else 1)
        records = [
            ControlPlaneSessionRecord(
                handle_digest=(index + 1).to_bytes(32, "big"),
                issuer=_ISSUER,
                subject="same-subject" if same_subject else f"subject-{index}",
                keycloak_session_id=_SID,
                actor_id=derive_human_actor_id(
                    _ISSUER, "same-subject" if same_subject else f"subject-{index}"
                ),
                roles=frozenset(("activate", "audit", "configure", "override", "read")),
                authority_profile_digest="a" * 64,
                issued_at=issued_at,
                expires_at=issued_at + timedelta(minutes=5 if expired else 10),
                display=DisplayMetadata("not-for-journal", "private-display"),
            )
            for index in range(count)
        ]
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO ci_coordinator.control_plane_sessions "
                "(handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
                "profile_digest, issued_at, expires_at, preferred_username, display_name) "
                "VALUES (%s, %s, %s, %s, %s, 31, %s, %s, %s, %s, %s)",
                [
                    (
                        record.handle_digest,
                        record.issuer,
                        record.subject,
                        record.keycloak_session_id,
                        record.actor_id if index % 2 == 0 else _FOREIGN_ACTOR,
                        record.authority_profile_digest,
                        record.issued_at,
                        record.expires_at,
                        record.display.preferred_username,
                        record.display.display_name,
                    )
                    for index, record in enumerate(records)
                ],
            )
    return records


async def _retained_handles(connection: AsyncConnection) -> set[bytes]:
    return set((await connection.execute(select(control_plane_sessions.c.handle_digest))).scalars())


async def _assert_events(
    connection: AsyncConnection, expected: list[tuple[ControlPlaneSessionRecord, str]]
) -> None:
    rows = (await connection.execute(select(activity_events))).mappings().all()
    assert Counter(
        (row["issuer"], row["subject"], row["actor"], row["action"], row["outcome"]) for row in rows
    ) == Counter(
        (record.issuer, record.subject, record.actor_id, action, "committed")
        for record, action in expected
    )
    assert all(row["actor"] != _FOREIGN_ACTOR for row in rows)
    assert all(row["retain_until"] - row["occurred_at"] == timedelta(days=30) for row in rows)
    assert "not-for-journal" not in repr(rows)
    assert "private-display" not in repr(rows)


@pytest.mark.parametrize("expired", [False, True])
def test_load_admits_current_identity_but_never_attributes_malformed_rows(
    runtime_postgres_database_url: str, postgres_database_url: str, expired: bool
) -> None:
    valid, malformed = _seed_sessions(postgres_database_url, 2, expired=expired)

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            assert await store.load(valid.handle_digest) == (None if expired else valid)
            assert await store.load(malformed.handle_digest) is None
            assert await store.load(malformed.handle_digest) is None
            if expired:
                assert await store.load(valid.handle_digest) is None
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == (
                    set() if expired else {valid.handle_digest}
                )
                await _assert_events(connection, [(valid, "expired")] if expired else [])
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["logout", "expired", "revoked", "replaced"])
def test_local_deletion_removes_malformed_rows_without_attribution_or_replay(
    runtime_postgres_database_url: str, postgres_database_url: str, reason: SessionEndReason
) -> None:
    valid, malformed = _seed_sessions(postgres_database_url, 2)

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            for record in (valid, malformed):
                await store.delete(record.handle_digest, reason=reason)
                await store.delete(record.handle_digest, reason=reason)
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == set()
                await _assert_events(connection, [(valid, reason)])
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("previous_index", [0, 1])
def test_previous_handle_replacement_attributes_only_the_admitted_deleted_row(
    runtime_postgres_database_url: str, postgres_database_url: str, previous_index: int
) -> None:
    records = _seed_sessions(postgres_database_url, 2)

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            now = await store.current_time()
            successor = replace(
                records[previous_index],
                handle_digest=b"n" * 32,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            await store.replace(
                previous_handle_digest=records[previous_index].handle_digest, record=successor
            )
            assert await store.load(successor.handle_digest) == successor
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == {
                    records[1 - previous_index].handle_digest,
                    successor.handle_digest,
                }
                expected = [(records[0], "replaced")] if previous_index == 0 else []
                await _assert_events(connection, [*expected, (successor, "login")])
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("expired", [False, True])
def test_replacement_preserves_original_cleanup_limit_and_capacity_selection(
    runtime_postgres_database_url: str, postgres_database_url: str, expired: bool
) -> None:
    records = _seed_sessions(postgres_database_url, 264, expired=expired, same_subject=not expired)

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            now = await store.current_time()
            successor = replace(
                records[0],
                handle_digest=b"n" * 32,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            await store.replace(previous_handle_digest=None, record=successor)
            # All fixture times tie: expiry keeps the tail, capacity keeps the newest seven handles.
            removed_count = 128 if expired else 257
            retained = records[removed_count:]
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == {
                    successor.handle_digest,
                    *(record.handle_digest for record in retained),
                }
                await _assert_events(
                    connection,
                    [
                        (record, "expired" if expired else "replaced")
                        for record in records[:removed_count:2]
                    ]
                    + [(successor, "login")],
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_backchannel_counts_all_actual_deletions_but_journals_only_valid_rows(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    records = _seed_sessions(postgres_database_url, 257)
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        connection.execute(
            "INSERT INTO ci_coordinator.control_plane_sessions "
            "(handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
            "preferred_username, display_name, profile_digest, issued_at, expires_at) "
            "SELECT %s, issuer, subject, 'other-sid', actor_id, roles, preferred_username, "
            "display_name, profile_digest, issued_at, expires_at "
            "FROM ci_coordinator.control_plane_sessions WHERE handle_digest = %s",
            (b"s" * 32, records[0].handle_digest),
        )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await PostgresControlPlaneSessionStore(engine).current_time()
            evidence = BackChannelLogoutEvidence(
                issuer=_ISSUER,
                token_id="multi-partition-logout",
                issued_at=now,
                expires_at=now + timedelta(minutes=1),
                target=BackChannelLogoutTarget(keycloak_session_id=_SID),
            )
            store = PostgresBackChannelLogoutStore(engine)
            result = await store.consume_and_delete(
                evidence=evidence, replay_retained_until=evidence.expires_at
            )
            assert result == BackChannelLogoutApplied(257)
            assert isinstance(
                await store.consume_and_delete(
                    evidence=evidence, replay_retained_until=evidence.expires_at
                ),
                BackChannelLogoutReplay,
            )
            distinct = replace(evidence, token_id="already-absent-target")
            assert await store.consume_and_delete(
                evidence=distinct, replay_retained_until=distinct.expires_at
            ) == BackChannelLogoutApplied(0)
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == {b"s" * 32}
                await _assert_events(connection, [(record, "revoked") for record in records[::2]])
                assert (
                    await connection.scalar(
                        select(func.count(func.distinct(activity_events.c.operation_ref)))
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_backchannel_does_not_count_a_selected_malformed_row_removed_by_current_load(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _seed_sessions(postgres_database_url, 257)
    original_stream = AsyncConnection.stream

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            now = await sessions.current_time()

            async def stream_after_cleanup(
                connection: AsyncConnection,
                statement: Executable,
                *,
                execution_options: Mapping[str, Any] | None = None,
            ) -> AsyncResult[Any]:
                selected = await original_stream(
                    connection, statement, execution_options=execution_options
                )
                try:
                    # Current malformed load can delete without taking the journal lock.
                    assert await sessions.load(records[1].handle_digest) is None
                except BaseException:
                    await selected.close()
                    raise
                return selected

            monkeypatch.setattr(AsyncConnection, "stream", stream_after_cleanup)
            evidence = BackChannelLogoutEvidence(
                issuer=_ISSUER,
                token_id="already-removed-selected-row",
                issued_at=now,
                expires_at=now + timedelta(minutes=1),
                target=BackChannelLogoutTarget(keycloak_session_id=_SID),
            )
            assert await PostgresBackChannelLogoutStore(engine).consume_and_delete(
                evidence=evidence, replay_retained_until=evidence.expires_at
            ) == BackChannelLogoutApplied(256)
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == set()
                await _assert_events(connection, [(record, "revoked") for record in records[::2]])
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_later_journal_failure_rolls_back_every_partition_and_replay_marker(
    runtime_postgres_database_url: str, postgres_database_url: str, cancelled: bool
) -> None:
    records = _seed_sessions(postgres_database_url, 257)
    journal_batches = 0
    observed_before_failure: list[tuple[int, int]] = []

    def fail_later(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal journal_batches
        if not statement.startswith("INSERT INTO ci_coordinator.activity_events"):
            return
        journal_batches += 1
        if journal_batches != 2:
            return
        retained = connection.scalar(select(func.count()).select_from(control_plane_sessions))
        journaled = connection.scalar(select(func.count()).select_from(activity_events))
        assert type(retained) is int and retained == 1
        assert type(journaled) is int and 0 < journaled < len(records[::2])
        assert (
            connection.scalar(select(func.count()).select_from(control_plane_logout_replays)) == 1
        )
        observed_before_failure.append((retained, journaled))
        if cancelled:
            raise asyncio.CancelledError
        connection.exec_driver_sql("SELECT 1 / 0")

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await PostgresControlPlaneSessionStore(engine).current_time()
            evidence = BackChannelLogoutEvidence(
                issuer=_ISSUER,
                token_id="rollback-all-partitions",
                issued_at=now,
                expires_at=now + timedelta(minutes=1),
                target=BackChannelLogoutTarget(keycloak_session_id=_SID),
            )
            async with engine.connect() as connection:
                retained_before = (
                    await connection.execute(
                        select(control_plane_sessions).order_by(
                            control_plane_sessions.c.handle_digest
                        )
                    )
                ).all()
            event.listen(engine.sync_engine, "before_cursor_execute", fail_later)
            store = PostgresBackChannelLogoutStore(engine)
            with pytest.raises(
                asyncio.CancelledError if cancelled else BackChannelLogoutStoreUnavailable
            ):
                await store.consume_and_delete(
                    evidence=evidence, replay_retained_until=evidence.expires_at
                )
            event.remove(engine.sync_engine, "before_cursor_execute", fail_later)
            assert journal_batches == 2 and len(observed_before_failure) == 1
            async with engine.connect() as connection:
                assert (
                    await connection.execute(
                        select(control_plane_sessions).order_by(
                            control_plane_sessions.c.handle_digest
                        )
                    )
                ).all() == retained_before
                await _assert_events(connection, [])
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(control_plane_logout_replays)
                    )
                    == 0
                )
            assert await store.consume_and_delete(
                evidence=evidence, replay_retained_until=evidence.expires_at
            ) == BackChannelLogoutApplied(257)
            async with engine.connect() as connection:
                assert await _retained_handles(connection) == set()
                await _assert_events(connection, [(record, "revoked") for record in records[::2]])
        finally:
            await engine.dispose()

    asyncio.run(scenario())
