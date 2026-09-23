from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutApplied,
    BackChannelLogoutEvidence,
    BackChannelLogoutReplay,
    BackChannelLogoutTarget,
    ControlPlaneRole,
    ControlPlaneSessionRecord,
    ControlPlaneSessionStoreRejected,
    DisplayMetadata,
    derive_human_actor_id,
)
from ci_coordinator.persistence import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
    control_plane_session_repository,
)
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    load_bundled_profile,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.control_plane_identity_schema_attestation import (
    control_plane_identity_schema_matches_contract,
)
from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string

pytestmark = pytest.mark.persistence

_ISSUER = "https://identity.example/realms/control-plane"
_OTHER_ISSUER = "https://other-identity.example/realms/control-plane"
_PROFILE_DIGEST = "a" * 64
_ROLES: frozenset[ControlPlaneRole] = frozenset(
    ("activate", "audit", "configure", "override", "read")
)


def test_session_round_trip_replaces_previous_handle_without_bearer_columns(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> tuple[ControlPlaneSessionRecord, ControlPlaneSessionRecord]:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            now = await store.current_time()
            previous = _record("previous", now)
            replacement = _record("replacement", now)
            await store.replace(previous_handle_digest=None, record=previous)
            await store.replace(
                previous_handle_digest=previous.handle_digest,
                record=replacement,
            )
            assert await store.load(previous.handle_digest) is None
            assert await store.load(replacement.handle_digest) == replacement
            await store.delete(replacement.handle_digest)
            assert await store.load(replacement.handle_digest) is None
            return previous, replacement
        finally:
            await engine.dispose()

    asyncio.run(scenario())

    with (
        psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'ci_coordinator' "
            "AND table_name = 'control_plane_sessions' ORDER BY ordinal_position"
        )
        columns = tuple(row[0] for row in cursor.fetchall())
    assert columns == (
        "handle_digest",
        "issuer",
        "subject",
        "keycloak_sid",
        "actor_id",
        "roles",
        "preferred_username",
        "display_name",
        "profile_digest",
        "issued_at",
        "expires_at",
    )
    assert not any(
        forbidden in column
        for column in columns
        for forbidden in ("access_token", "github", "id_token", "refresh_token")
    )


def test_replacement_enforces_eight_sessions_per_exact_issuer_subject(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            now = await store.current_time()
            for index in range(9):
                await store.replace(
                    previous_handle_digest=None,
                    record=_record(f"same-identity-{index}", now),
                )
            await store.replace(
                previous_handle_digest=None,
                record=_record(
                    "other-issuer",
                    now,
                    issuer=_OTHER_ISSUER,
                ),
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())

    with (
        psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT issuer, count(*) FROM ci_coordinator.control_plane_sessions "
            "WHERE subject = 'subject-a' GROUP BY issuer ORDER BY issuer"
        )
        assert cursor.fetchall() == [(_ISSUER, 8), (_OTHER_ISSUER, 1)]


def test_replacement_and_expiry_cleanup_commit_or_roll_back_together(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        migration_engine = create_postgres_engine(postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(runtime_engine)
            now = await store.current_time()
            previous = _record("atomic-previous", now)
            collision = _record("atomic-collision", now, subject="subject-b")
            conflicting_replacement = _record("atomic-collision", now)
            expired = _record(
                "atomic-expired",
                now - timedelta(minutes=10),
                subject="subject-expired",
                lifetime_seconds=300,
            )
            await store.replace(previous_handle_digest=None, record=previous)
            await store.replace(previous_handle_digest=None, record=collision)
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO ci_coordinator.control_plane_sessions "
                        "(handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
                        "preferred_username, display_name, profile_digest, issued_at, expires_at) "
                        "VALUES (:handle_digest, :issuer, :subject, :keycloak_sid, :actor_id, "
                        ":roles, :preferred_username, :display_name, :profile_digest, "
                        ":issued_at, :expires_at)"
                    ),
                    {
                        "handle_digest": expired.handle_digest,
                        "issuer": expired.issuer,
                        "subject": expired.subject,
                        "keycloak_sid": expired.keycloak_session_id,
                        "actor_id": expired.actor_id,
                        "roles": 31,
                        "preferred_username": expired.display.preferred_username,
                        "display_name": expired.display.display_name,
                        "profile_digest": expired.authority_profile_digest,
                        "issued_at": expired.issued_at,
                        "expires_at": expired.expires_at,
                    },
                )

            with pytest.raises(ControlPlaneSessionStoreRejected):
                await store.replace(
                    previous_handle_digest=previous.handle_digest,
                    record=conflicting_replacement,
                )
            async with migration_engine.connect() as connection:
                retained = await connection.scalar(
                    text(
                        "SELECT count(*) FROM ci_coordinator.control_plane_sessions "
                        "WHERE handle_digest IN (:previous, :collision, :expired)"
                    ),
                    {
                        "previous": previous.handle_digest,
                        "collision": collision.handle_digest,
                        "expired": expired.handle_digest,
                    },
                )
                assert retained == 3

            replacement = _record("atomic-replacement", now)
            await store.replace(
                previous_handle_digest=previous.handle_digest,
                record=replacement,
            )
            assert await store.load(previous.handle_digest) is None
            assert await store.load(replacement.handle_digest) == replacement
            assert await store.load(collision.handle_digest) == collision
            async with migration_engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM ci_coordinator.control_plane_sessions "
                            "WHERE handle_digest = :expired"
                        ),
                        {"expired": expired.handle_digest},
                    )
                    == 0
                )
        finally:
            await migration_engine.dispose()
            await runtime_engine.dispose()

    asyncio.run(scenario())


def test_malformed_derived_actor_row_is_deleted_instead_of_authenticating(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    digest = sha256(b"malformed-actor").digest()
    with (
        psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO ci_coordinator.control_plane_sessions "
            "(handle_digest, issuer, subject, keycloak_sid, actor_id, roles, "
            "profile_digest, issued_at, expires_at) VALUES "
            "(%s, %s, 'subject-a', 'sid-a', %s, 16, %s, "
            "statement_timestamp(), statement_timestamp() + INTERVAL '5 minutes')",
            (
                digest,
                _ISSUER,
                derive_human_actor_id(_ISSUER, "different-subject"),
                _PROFILE_DIGEST,
            ),
        )

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            assert await PostgresControlPlaneSessionStore(engine).load(digest) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())

    with (
        psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT count(*) FROM ci_coordinator.control_plane_sessions WHERE handle_digest = %s",
            (digest,),
        )
        assert cursor.fetchone() == (0,)


@pytest.mark.parametrize("operation", ["load", "replace", "clock"])
def test_session_operation_uses_time_after_compatibility_fence(
    runtime_postgres_database_url: str,
    operation: Literal["load", "replace", "clock"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        blocker = await psycopg.AsyncConnection.connect(
            to_psycopg_connection_string(runtime_postgres_database_url)
        )
        try:
            store = PostgresControlPlaneSessionStore(engine)
            now = await store.current_time()
            record = _record("fence-expiry", now, lifetime_seconds=2)
            if operation == "load":
                await store.replace(previous_handle_digest=None, record=record)
            profile = load_bundled_profile()
            await blocker.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(%s, %s)",
                (profile.fence_class_id, profile.fence_object_id),
            )
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def observe_fence(
                connection: AsyncConnection,
                selected_profile: CompatibilityProfile,
                mode: CompatibilityFenceMode,
            ) -> None:
                pid = await connection.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
                assert type(pid) is int
                waiter_pid.set_result(pid)
                await acquire_compatibility_fence(connection, selected_profile, mode)

            async def exercise_operation() -> None:
                if operation == "load":
                    assert await store.load(record.handle_digest) is None
                elif operation == "replace":
                    with pytest.raises(ControlPlaneSessionStoreRejected):
                        await store.replace(previous_handle_digest=None, record=record)
                else:
                    assert await store.current_time() >= record.expires_at

            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(
                    control_plane_session_repository, "acquire_compatibility_fence", observe_fence
                )
                async with asyncio.timeout(15), asyncio.TaskGroup() as group:
                    group.create_task(exercise_operation())
                    try:
                        pid = await waiter_pid
                        await _wait_for_blocker(blocker, pid)
                        assert await _database_time(blocker) < record.expires_at
                        await _wait_past_expiry(blocker, record.expires_at)
                    finally:
                        await blocker.rollback()
            if operation == "replace":
                assert await store.load(record.handle_digest) is None
        finally:
            if blocker.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                await blocker.rollback()
            await blocker.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("target_kind", "expected_deleted"),
    [
        ("sid", frozenset(("exact", "sid-peer"))),
        ("subject", frozenset(("exact", "subject-peer"))),
        ("both", frozenset(("exact",))),
    ],
)
def test_back_channel_logout_uses_exact_issuer_and_target_conjunction(
    runtime_postgres_database_url: str,
    target_kind: Literal["sid", "subject", "both"],
    expected_deleted: frozenset[str],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            logouts = PostgresBackChannelLogoutStore(engine)
            now = await sessions.current_time()
            records = {
                "exact": _record("target-exact", now),
                "sid-peer": _record("target-sid-peer", now, subject="subject-b"),
                "subject-peer": _record("target-subject-peer", now, sid="sid-b"),
                "other-issuer": _record(
                    "target-other-issuer",
                    now,
                    issuer=_OTHER_ISSUER,
                ),
            }
            for record in records.values():
                await sessions.replace(previous_handle_digest=None, record=record)
            target = BackChannelLogoutTarget(
                keycloak_session_id=None if target_kind == "subject" else "sid-a",
                subject=None if target_kind == "sid" else "subject-a",
            )
            mutation = await logouts.consume_and_delete(
                evidence=_logout_evidence(now, target, token_id=f"logout-{target_kind}"),
                replay_retained_until=now + timedelta(minutes=3),
            )
            assert mutation == BackChannelLogoutApplied(len(expected_deleted))
            for label, record in records.items():
                retained = await sessions.load(record.handle_digest)
                assert (retained is None) is (label in expected_deleted)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_logout_replay_marker_prevents_a_later_session_delete(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(engine)
            logouts = PostgresBackChannelLogoutStore(engine)
            now = await sessions.current_time()
            first = _record("replay-first", now)
            evidence = _logout_evidence(
                now,
                BackChannelLogoutTarget(keycloak_session_id="sid-a"),
                token_id="logout-replay",
            )
            await sessions.replace(previous_handle_digest=None, record=first)
            assert await logouts.consume_and_delete(
                evidence=evidence,
                replay_retained_until=now + timedelta(minutes=3),
            ) == BackChannelLogoutApplied(1)

            later = _record("replay-later", now)
            await sessions.replace(previous_handle_digest=None, record=later)
            assert (
                type(
                    await logouts.consume_and_delete(
                        evidence=evidence,
                        replay_retained_until=now + timedelta(minutes=3),
                    )
                )
                is BackChannelLogoutReplay
            )
            assert await sessions.load(later.handle_digest) == later
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_control_plane_identity_schema_and_runtime_acl_are_exact(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def seed_session() -> bytes:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = PostgresControlPlaneSessionStore(engine)
            now = await store.current_time()
            record = _record("runtime-acl-witness", now)
            await store.replace(previous_handle_digest=None, record=record)
            return record.handle_digest
        finally:
            await engine.dispose()

    async def schema_matches() -> bool:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with engine.connect() as connection:
                return await control_plane_identity_schema_matches_contract(connection)
        finally:
            await engine.dispose()

    assert asyncio.run(schema_matches())
    handle_digest = asyncio.run(seed_session())
    with (
        psycopg.connect(to_psycopg_connection_string(runtime_postgres_database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_sessions', 'SELECT'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_sessions', 'INSERT'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_sessions', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_sessions', 'UPDATE'), "
            "has_column_privilege(current_user, "
            "'ci_coordinator.control_plane_sessions', 'handle_digest', 'UPDATE'), "
            "has_column_privilege(current_user, "
            "'ci_coordinator.control_plane_sessions', 'roles', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_logout_replays', 'SELECT'), "
            "has_any_column_privilege(current_user, "
            "'ci_coordinator.control_plane_logout_replays', 'SELECT'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_logout_replays', 'INSERT'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_logout_replays', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'ci_coordinator.control_plane_logout_replays', 'UPDATE')"
        )
        assert cursor.fetchone() == (
            True,
            True,
            True,
            False,
            True,
            False,
            False,
            True,
            True,
            True,
            False,
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "UPDATE ci_coordinator.control_plane_sessions SET handle_digest = handle_digest "
                "WHERE handle_digest = %s",
                (handle_digest,),
            )

    async def guard_is_required() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.control_plane_sessions "
                        "DISABLE TRIGGER tr_control_plane_sessions_immutable"
                    )
                )
                assert not await control_plane_identity_schema_matches_contract(connection)
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.control_plane_sessions "
                        "ENABLE TRIGGER tr_control_plane_sessions_immutable"
                    )
                )
                assert await control_plane_identity_schema_matches_contract(connection)
        finally:
            await engine.dispose()

    asyncio.run(guard_is_required())


def _record(
    identity: str,
    issued_at: datetime,
    *,
    issuer: str = _ISSUER,
    subject: str = "subject-a",
    sid: str = "sid-a",
    lifetime_seconds: int = 600,
) -> ControlPlaneSessionRecord:
    return ControlPlaneSessionRecord(
        handle_digest=sha256(identity.encode("ascii")).digest(),
        issuer=issuer,
        subject=subject,
        keycloak_session_id=sid,
        actor_id=derive_human_actor_id(issuer, subject),
        roles=_ROLES,
        authority_profile_digest=_PROFILE_DIGEST,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=lifetime_seconds),
        display=DisplayMetadata(
            preferred_username="operator",
            display_name="Control Plane Operator",
        ),
    )


def _logout_evidence(
    issued_at: datetime,
    target: BackChannelLogoutTarget,
    *,
    token_id: str,
) -> BackChannelLogoutEvidence:
    return BackChannelLogoutEvidence(
        issuer=_ISSUER,
        token_id=token_id,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=1),
        target=target,
    )


async def _wait_past_expiry(
    connection: psycopg.AsyncConnection[Any],
    expires_at: datetime,
) -> None:
    while await _database_time(connection) < expires_at:  # noqa: ASYNC110 -- No local DB-time event.
        await asyncio.sleep(0.01)


async def _wait_for_blocker(connection: psycopg.AsyncConnection[Any], waiter_pid: int) -> None:
    while True:
        result = await connection.execute(
            "SELECT %s = ANY(pg_catalog.pg_blocking_pids(%s))",
            (connection.info.backend_pid, waiter_pid),
        )
        if await result.fetchone() == (True,):
            return
        await asyncio.sleep(0.01)


async def _database_time(connection: psycopg.AsyncConnection[Any]) -> datetime:
    result = await connection.execute("SELECT statement_timestamp()")
    row = await result.fetchone()
    assert row is not None and type(row[0]) is datetime
    value: datetime = row[0]
    assert value.tzinfo is not None and value.utcoffset() is not None
    return value
