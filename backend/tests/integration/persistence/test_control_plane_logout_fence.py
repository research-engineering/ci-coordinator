"""Native witnesses for the durable logout revocation fence (ROADMAP CI-015)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.control_plane_identity import (
    LOGOUT_FENCE_RETENTION_SECONDS,
    BackChannelLogoutApplied,
    BackChannelLogoutReplay,
    BackChannelLogoutTarget,
    ControlPlaneSessionStoreRejected,
    LoginStartAnchor,
)
from ci_coordinator.control_plane_identity.activity import SessionDeletionReason
from ci_coordinator.persistence import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
    control_plane_session_repository,
)
from ci_coordinator.persistence._schema_activity import activity_events
from ci_coordinator.persistence._schema_control_plane_identity import (
    control_plane_logout_fence_generation,
    control_plane_logout_fences,
)
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string

from .test_control_plane_identity_state import (
    _ISSUER,
    _logout_evidence,
    _record,
    _wait_for_blocker,
)

pytestmark = pytest.mark.persistence

_FENCE_LOCK = (
    "SELECT pg_catalog.pg_advisory_xact_lock("
    "pg_catalog.hashtextextended('control-plane-logout-fence/v1', 0))"
)
_FENCE_SUBJECT_A = (
    "WITH advanced AS (UPDATE ci_coordinator.control_plane_logout_fence_generation "
    "SET current_generation = current_generation + 1 RETURNING current_generation) "
    "INSERT INTO ci_coordinator.control_plane_logout_fences "
    "(issuer, target_kind, target_value, generation, fenced_at) "
    "SELECT %s, 'subject', 'subject-a', current_generation, statement_timestamp() FROM advanced"
)
_RETENTION = f"{LOGOUT_FENCE_RETENTION_SECONDS} seconds"


def test_local_logout_fences_earlier_login_starts_of_its_subject_only(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            first_start = await store.login_start_anchor()
            first = _record("fence-first", first_start.started_at)
            await store.replace(previous_handle_digest=None, record=first, login_start=first_start)
            # A second tab started its login transaction before the logout committed.
            pending_start = await store.login_start_anchor()
            await _logout(store, first.handle_digest, "subject-a")

            late = _record("fence-late", await store.current_time())
            with pytest.raises(ControlPlaneSessionStoreRejected):
                await store.replace(
                    previous_handle_digest=None, record=late, login_start=pending_start
                )
            assert await store.load(late.handle_digest) is None

            other = _record("fence-other-subject", await store.current_time(), subject="subject-b")
            await store.replace(
                previous_handle_digest=None, record=other, login_start=pending_start
            )
            assert await store.load(other.handle_digest) == other

            renewed_start = await store.login_start_anchor()
            renewed = _record("fence-renewed", renewed_start.started_at)
            await store.replace(
                previous_handle_digest=None, record=renewed, login_start=renewed_start
            )
            assert await store.load(renewed.handle_digest) == renewed

            async with engine.connect() as connection:
                actions = await _subject_actions(connection, "subject-a")
                fences = await _fences(connection)
            assert actions == ["login", "logout", "login"]
            assert fences == [(_ISSUER, "subject", "subject-a", 1)]
            assert pending_start.fence_generation == 0
            assert renewed_start.fence_generation == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_logout_fences_the_authenticated_subject_after_a_concurrent_replacement(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            first_start = await store.login_start_anchor()
            first = _record("authenticated", first_start.started_at)
            await store.replace(previous_handle_digest=None, record=first, login_start=first_start)
            replacing_start = await store.login_start_anchor()
            late_start = await store.login_start_anchor()

            authenticated = await store.load(first.handle_digest)
            assert authenticated is not None and authenticated == first
            # Another tab's callback replaces the authenticated handle before logout runs.
            replacement = _record("replacement", await store.current_time())
            await store.replace(
                previous_handle_digest=first.handle_digest,
                record=replacement,
                login_start=replacing_start,
            )
            assert await store.load(first.handle_digest) is None
            await store.logout(
                first.handle_digest, issuer=authenticated.issuer, subject=authenticated.subject
            )

            late = _record("late-callback", await store.current_time())
            with pytest.raises(ControlPlaneSessionStoreRejected):
                await store.replace(
                    previous_handle_digest=None, record=late, login_start=late_start
                )
            assert await store.load(late.handle_digest) is None
            # Local logout fences in-flight logins only; the committed replacement survives.
            assert await store.load(replacement.handle_digest) == replacement
            async with engine.connect() as connection:
                actions = await _subject_actions(connection, "subject-a")
                fences = await _fences(connection)
            assert actions == ["login", "replaced", "login"]
            assert fences == [(_ISSUER, "subject", "subject-a", 1)]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["expired", "revoked", "replaced"])
def test_non_logout_deletions_write_no_fence(
    runtime_postgres_database_url: str,
    reason: SessionDeletionReason,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            start = await store.login_start_anchor()
            deleted = _record(f"fence-{reason}", start.started_at)
            await store.replace(previous_handle_digest=None, record=deleted, login_start=start)
            await store.delete(deleted.handle_digest, reason=reason)
            assert await store.load(deleted.handle_digest) is None
            successor = _record("fence-successor", await store.current_time())
            await store.replace(previous_handle_digest=None, record=successor, login_start=start)
            assert await store.load(successor.handle_digest) == successor
            async with engine.connect() as connection:
                # Activity proves the deletion removed a row; it still wrote no fence.
                assert await _subject_actions(connection, "subject-a") == [
                    "login",
                    reason,
                    "login",
                ]
                assert await _fences(connection) == []
                assert await _generation(connection) == (0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("target_kind", "fenced", "unfenced"),
    [
        ("sid", {"same-sid-other-subject"}, {"other-sid-same-subject"}),
        ("subject", {"other-sid-same-subject"}, {"same-sid-other-subject"}),
        ("both", {"same-sid-other-subject"}, {"other-sid-same-subject"}),
    ],
)
def test_back_channel_logout_fences_its_target_even_without_deleted_rows(
    runtime_postgres_database_url: str,
    target_kind: Literal["sid", "subject", "both"],
    fenced: set[str],
    unfenced: set[str],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            logouts = PostgresBackChannelLogoutStore(engine)
            start = await sessions.login_start_anchor()
            target = BackChannelLogoutTarget(
                keycloak_session_id=None if target_kind == "subject" else "sid-a",
                subject=None if target_kind == "sid" else "subject-a",
            )
            # The exchange finished before the logout, but no session row exists yet.
            assert await logouts.consume_and_delete(
                evidence=_logout_evidence(
                    start.started_at, target, token_id=f"fence-{target_kind}"
                ),
                replay_retained_until=start.started_at + timedelta(minutes=3),
            ) == BackChannelLogoutApplied(0)
            now = await sessions.current_time()
            candidates = {
                "exact": _record("bc-exact", now),
                "same-sid-other-subject": _record("bc-sid", now, subject="subject-b"),
                "other-sid-same-subject": _record("bc-subject", now, sid="sid-b"),
                "other-issuer": _record(
                    "bc-issuer", now, issuer="https://other-identity.example/realms/control-plane"
                ),
            }
            for label, record in candidates.items():
                if label == "exact" or label in fenced:
                    with pytest.raises(ControlPlaneSessionStoreRejected):
                        await sessions.replace(
                            previous_handle_digest=None, record=record, login_start=start
                        )
                    assert await sessions.load(record.handle_digest) is None
                else:
                    assert label in unfenced or label == "other-issuer"
                    await sessions.replace(
                        previous_handle_digest=None, record=record, login_start=start
                    )
                    assert await sessions.load(record.handle_digest) == record
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_back_channel_replay_does_not_advance_the_fence(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            logouts = PostgresBackChannelLogoutStore(engine)
            now = await sessions.current_time()
            evidence = _logout_evidence(
                now, BackChannelLogoutTarget(subject="subject-a"), token_id="fence-replay"
            )
            assert await logouts.consume_and_delete(
                evidence=evidence, replay_retained_until=now + timedelta(minutes=3)
            ) == BackChannelLogoutApplied(0)
            between = await sessions.login_start_anchor()
            async with engine.connect() as connection:
                first_fences = await _fences(connection)
            assert isinstance(
                await logouts.consume_and_delete(
                    evidence=evidence, replay_retained_until=now + timedelta(minutes=3)
                ),
                BackChannelLogoutReplay,
            )
            async with engine.connect() as connection:
                assert await _fences(connection) == first_fences
                assert await _generation(connection) == (1, 0)
            record = _record("fence-after-replay", await sessions.current_time())
            await sessions.replace(previous_handle_digest=None, record=record, login_start=between)
            assert await sessions.load(record.handle_digest) == record
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("anchor", ["future", "beyond-retention"])
def test_login_start_outside_the_age_bound_is_rejected(
    runtime_postgres_database_url: str,
    anchor: Literal["future", "beyond-retention"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            current = await store.login_start_anchor()
            started_at = (
                current.started_at + timedelta(minutes=1)
                if anchor == "future"
                else current.started_at - timedelta(seconds=LOGOUT_FENCE_RETENTION_SECONDS)
            )
            record = _record(f"fence-window-{anchor}", await store.current_time())
            with pytest.raises(ControlPlaneSessionStoreRejected):
                await store.replace(
                    previous_handle_digest=None,
                    record=record,
                    login_start=LoginStartAnchor(
                        fence_generation=current.fence_generation, started_at=started_at
                    ),
                )
            assert await store.load(record.handle_digest) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generation_not_database_time_orders_logins_against_logouts(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            before = await store.login_start_anchor()
            await _logout(store, sha256(b"absent-handle").digest(), "subject-a")
            after = await store.login_start_anchor()
            # The database clock stepped back before the logout wrote its fence.
            _set_fence_instant(postgres_database_url, before.started_at - timedelta(seconds=1))
            late = _record("regressed-late", await store.current_time())
            with pytest.raises(ControlPlaneSessionStoreRejected):
                await store.replace(previous_handle_digest=None, record=late, login_start=before)
            assert await store.load(late.handle_digest) is None

            # The database clock ran ahead when the logout wrote its fence.
            _set_fence_instant(postgres_database_url, after.started_at + timedelta(minutes=1))
            renewed = _record("regressed-renewed", await store.current_time())
            await store.replace(previous_handle_digest=None, record=renewed, login_start=after)
            assert await store.load(renewed.handle_digest) == renewed
            assert (before.fence_generation, after.fence_generation) == (0, 1)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_fence_cleanup_raises_the_floor_so_erased_fences_never_resurrect_older_logins(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            pending = await store.login_start_anchor()
            await _logout(store, sha256(b"absent-a").digest(), "subject-a")
            # The database clock jumps past retention before the next fence write cleans up.
            with (
                psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as admin,
                admin.cursor() as cursor,
            ):
                cursor.execute(
                    "UPDATE ci_coordinator.control_plane_logout_fences "
                    "SET fenced_at = fenced_at - CAST(%s AS interval) - INTERVAL '1 second'",
                    (_RETENTION,),
                )
            await _logout(store, sha256(b"absent-b").digest(), "subject-b")
            async with engine.connect() as connection:
                assert await _fences(connection) == [(_ISSUER, "subject", "subject-b", 2)]
                assert await _generation(connection) == (2, 1)

            async def refuse_pending_evidence(phase: str) -> None:
                for subject in ("subject-a", "subject-c"):
                    stale = _record(
                        f"floor-{phase}-{subject}", await store.current_time(), subject=subject
                    )
                    with pytest.raises(ControlPlaneSessionStoreRejected):
                        await store.replace(
                            previous_handle_digest=None, record=stale, login_start=pending
                        )
                    assert await store.load(stale.handle_digest) is None

            # Wall time has not moved, so only the floor still refuses the erased evidence.
            await refuse_pending_evidence("cleanup")

            # A later fence write that erases nothing keeps the floor, so it stays effective.
            await _logout(store, sha256(b"absent-b-again").digest(), "subject-b")
            async with engine.connect() as connection:
                assert await _fences(connection) == [(_ISSUER, "subject", "subject-b", 3)]
                assert await _generation(connection) == (3, 1)
            await refuse_pending_evidence("no-erasure")
            renewed_start = await store.login_start_anchor()
            renewed = _record("floor-renewed", renewed_start.started_at)
            await store.replace(
                previous_handle_digest=None, record=renewed, login_start=renewed_start
            )
            assert await store.load(renewed.handle_digest) == renewed
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_login_start_anchor_waits_for_and_follows_an_in_flight_fence_writer(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = PostgresControlPlaneSessionStore(engine)
        writer = await psycopg.AsyncConnection.connect(
            to_psycopg_connection_string(postgres_database_url)
        )
        try:
            await writer.execute(_FENCE_LOCK)
            started = await _while_waiting_for(
                writer, monkeypatch, store.login_start_anchor, after=_commit_fence(writer)
            )
            assert started.fence_generation == await _fence_generation(engine) == 1
            record = _record("fence-start-after-writer", started.started_at)
            await store.replace(previous_handle_digest=None, record=record, login_start=started)
            assert await store.load(record.handle_digest) == record
        finally:
            await _close(writer)
            await engine.dispose()

    asyncio.run(scenario())


def test_session_insertion_waits_for_an_in_flight_fence_writer_then_rejects(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = PostgresControlPlaneSessionStore(engine)
        writer = await psycopg.AsyncConnection.connect(
            to_psycopg_connection_string(postgres_database_url)
        )
        try:
            started = await store.login_start_anchor()
            record = _record("fence-insert-during-writer", await store.current_time())
            await writer.execute(_FENCE_LOCK)

            async def insert() -> None:
                await store.replace(previous_handle_digest=None, record=record, login_start=started)

            with pytest.raises(ControlPlaneSessionStoreRejected):
                await _while_waiting_for(writer, monkeypatch, insert, after=_commit_fence(writer))
            assert await store.load(record.handle_digest) is None
        finally:
            await _close(writer)
            await engine.dispose()

    asyncio.run(scenario())


def test_fence_writes_erase_only_a_bounded_set_of_expired_fences(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    with (
        psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE ci_coordinator.control_plane_logout_fence_generation "
            "SET current_generation = 131"
        )
        cursor.execute(
            "INSERT INTO ci_coordinator.control_plane_logout_fences "
            "(issuer, target_kind, target_value, generation, fenced_at) "
            "SELECT %s, 'subject', 'expired-' || item, item, "
            "statement_timestamp() - CAST(%s AS interval) - INTERVAL '1 second' "
            "FROM generate_series(1, 130) AS item",
            (_ISSUER, _RETENTION),
        )
        cursor.execute(
            "INSERT INTO ci_coordinator.control_plane_logout_fences "
            "(issuer, target_kind, target_value, generation, fenced_at) VALUES "
            "(%s, 'subject', 'retained', 131, "
            "statement_timestamp() - CAST(%s AS interval) + INTERVAL '1 minute')",
            (_ISSUER, _RETENTION),
        )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            start = await store.login_start_anchor()
            record = _record("fence-cleanup", start.started_at)
            await store.replace(previous_handle_digest=None, record=record, login_start=start)
            await _logout(store, record.handle_digest, "subject-a")
            async with engine.connect() as connection:
                fences = {row[2]: row[3] for row in await _fences(connection)}
                generation = await _generation(connection)
            survivors = {value for value in fences if value.startswith("expired-")}
            assert len(survivors) == 2
            assert {"retained", "subject-a"} <= set(fences)
            erased = set(range(1, 131)) - {fences[value] for value in survivors}
            assert generation == (132, max(erased))
            assert fences["subject-a"] == 132
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def _logout(store: PostgresControlPlaneSessionStore, digest: bytes, subject: str) -> None:
    await store.logout(digest, issuer=_ISSUER, subject=subject)


async def _subject_actions(connection: AsyncConnection, subject: str) -> list[str]:
    return list(
        await connection.scalars(
            select(activity_events.c.action)
            .where(activity_events.c.subject == subject)
            .order_by(activity_events.c.sequence)
        )
    )


async def _fences(connection: AsyncConnection) -> list[tuple[str, str, str, int]]:
    fences = control_plane_logout_fences
    rows = await connection.execute(
        select(
            fences.c.issuer, fences.c.target_kind, fences.c.target_value, fences.c.generation
        ).order_by(fences.c.generation)
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def _generation(connection: AsyncConnection) -> tuple[int, int]:
    counter = control_plane_logout_fence_generation
    row = (
        await connection.execute(
            select(counter.c.current_generation, counter.c.deleted_generation_floor)
        )
    ).one()
    return row[0], row[1]


def _set_fence_instant(database_url: str, fenced_at: datetime) -> None:
    with (
        psycopg.connect(to_psycopg_connection_string(database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE ci_coordinator.control_plane_logout_fences SET fenced_at = %s", (fenced_at,)
        )


async def _while_waiting_for[T](
    writer: psycopg.AsyncConnection[Any],
    monkeypatch: pytest.MonkeyPatch,
    operation: Callable[[], Coroutine[Any, Any, T]],
    *,
    after: Callable[[], Awaitable[None]],
) -> T:
    waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def observe_fence(
        connection: AsyncConnection,
        selected_profile: CompatibilityProfile,
        mode: CompatibilityFenceMode,
    ) -> None:
        pid = await connection.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
        assert type(pid) is int
        if not waiter_pid.done():
            waiter_pid.set_result(pid)
        await acquire_compatibility_fence(connection, selected_profile, mode)

    with monkeypatch.context() as patch:
        # noinspection PyUnresolvedReferences
        patch.setattr(
            control_plane_session_repository, "acquire_compatibility_fence", observe_fence
        )
        async with asyncio.timeout(15):
            task = asyncio.create_task(operation())
            try:
                await _wait_for_blocker(writer, await waiter_pid)
                assert not task.done()
                await after()
            except BaseException:
                task.cancel()
                raise
            return await task


def _commit_fence(writer: psycopg.AsyncConnection[Any]) -> Callable[[], Awaitable[None]]:
    async def commit() -> None:
        await writer.execute(_FENCE_SUBJECT_A, (_ISSUER,))
        await writer.commit()

    return commit


async def _fence_generation(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        value = await connection.scalar(
            select(control_plane_logout_fences.c.generation).where(
                control_plane_logout_fences.c.target_value == "subject-a"
            )
        )
    assert type(value) is int
    return value


async def _close(writer: psycopg.AsyncConnection[Any]) -> None:
    if writer.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        await writer.rollback()
    await writer.close()
