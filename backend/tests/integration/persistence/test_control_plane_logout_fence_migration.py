"""Native migration, catalog and ACL witnesses for control-plane-logout-fence/v1."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutStoreUnavailable,
    BackChannelLogoutTarget,
    ControlPlaneSessionStoreUnavailable,
)
from ci_coordinator.persistence import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
    migration_result_attestation,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.control_plane_logout_fence_schema_contract import (
    control_plane_logout_fence_schema_matches_sync,
)
from ci_coordinator.persistence.schema_capabilities import CONTROL_PLANE_LOGOUT_FENCE

from .conftest import alembic_config
from .test_control_plane_identity_state import _logout_evidence, _record

pytestmark = pytest.mark.persistence

_PREDECESSOR = "20260926_0017"
_SUCCESSOR = "20261011_0018"
_TABLE = "ci_coordinator.control_plane_logout_fences"
_GENERATION = "ci_coordinator.control_plane_logout_fence_generation"
_RUNTIME = "ci_coordinator_runtime_test"


def _capabilities(connection: Connection, revision: str) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            text(
                "SELECT capability_id, descriptor_hash FROM "
                "ci_coordinator.database_compatibility_capabilities WHERE revision_id = :revision"
            ),
            {"revision": revision},
        )
    }


def test_forward_0018_is_atomic_preserves_0017_and_adds_only_the_fence(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.connect() as connection:
            prior = _capabilities(connection, _PREDECESSOR)
        with monkeypatch.context() as patch:
            patch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                CONTROL_PLANE_LOGOUT_FENCE.capability_id,
                lambda _connection: False,
            )
            with pytest.raises(RuntimeError, match="control-plane-logout-fence"):
                command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == _PREDECESSOR
            )
            assert connection.scalar(text(f"SELECT to_regclass('{_TABLE}')")) is None
            assert connection.scalar(text(f"SELECT to_regclass('{_GENERATION}')")) is None
            assert _capabilities(connection, _PREDECESSOR) == prior
            assert _capabilities(connection, _SUCCESSOR) == set()
        command.upgrade(config, _SUCCESSOR)
        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert control_plane_logout_fence_schema_matches_sync(connection)
            added = CONTROL_PLANE_LOGOUT_FENCE.declaration()
            assert _capabilities(connection, _SUCCESSOR) == prior | {
                (added.capability_id, added.descriptor_hash)
            }
            assert connection.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0
            assert [
                tuple(row) for row in connection.execute(text(f"SELECT * FROM {_GENERATION}"))
            ] == [(True, 0, 0)]
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _PREDECESSOR)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("damage", "repair"),
    [
        (
            f"ALTER TABLE {_TABLE} ALTER COLUMN fenced_at DROP NOT NULL",
            f"ALTER TABLE {_TABLE} ALTER COLUMN fenced_at SET NOT NULL",
        ),
        (
            f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_control_plane_logout_fences_target_kind",
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_control_plane_logout_fences_target_kind "
            "CHECK (target_kind IN ('subject', 'sid'))",
        ),
        (
            f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_control_plane_logout_fences_generation",
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_control_plane_logout_fences_generation "
            "CHECK (generation BETWEEN 1 AND 9007199254740991)",
        ),
        (
            f"ALTER TABLE {_TABLE} DROP CONSTRAINT ck_control_plane_logout_fences_fenced_at",
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_control_plane_logout_fences_fenced_at "
            "CHECK (isfinite(fenced_at))",
        ),
        (
            f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY",
        ),
        (
            f"ALTER TABLE {_GENERATION} "
            "DROP CONSTRAINT ck_control_plane_logout_fence_generation_order",
            f"ALTER TABLE {_GENERATION} "
            "ADD CONSTRAINT ck_control_plane_logout_fence_generation_order "
            "CHECK (deleted_generation_floor BETWEEN 0 AND current_generation "
            "AND current_generation <= 9007199254740991)",
        ),
        (
            f"DELETE FROM {_GENERATION}",
            f"INSERT INTO {_GENERATION} "
            "(singleton, current_generation, deleted_generation_floor) VALUES (true, 0, 0)",
        ),
    ],
    ids=(
        "fenced-at-nullable",
        "target-kind-check",
        "generation-check",
        "fenced-at-check",
        "row-level-security",
        "generation-order-check",
        "generation-row-missing",
    ),
)
def test_damaged_fence_fails_closed_for_login_and_logout_but_not_authentication(
    postgres_database_url: str, runtime_postgres_database_url: str, damage: str, repair: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            sessions = PostgresControlPlaneSessionStore(runtime)
            logouts = PostgresBackChannelLogoutStore(runtime)
            start = await sessions.login_start_anchor()
            record = _record("fence-damage", start.started_at)
            await sessions.replace(previous_handle_digest=None, record=record, login_start=start)
            async with admin.begin() as connection:
                await connection.execute(text(damage))
            try:
                with pytest.raises(ControlPlaneSessionStoreUnavailable):
                    await sessions.login_start_anchor()
                successor = _record("fence-damage-successor", await sessions.current_time())
                with pytest.raises(ControlPlaneSessionStoreUnavailable):
                    await sessions.replace(
                        previous_handle_digest=None, record=successor, login_start=start
                    )
                with pytest.raises(ControlPlaneSessionStoreUnavailable):
                    await sessions.logout(
                        record.handle_digest, issuer=record.issuer, subject=record.subject
                    )
                with pytest.raises(BackChannelLogoutStoreUnavailable):
                    await logouts.consume_and_delete(
                        evidence=_logout_evidence(
                            start.started_at,
                            BackChannelLogoutTarget(subject="subject-a"),
                            token_id="damage",
                        ),
                        replay_retained_until=start.started_at + timedelta(minutes=3),
                    )
                # Fence catalog damage is capability-specific: existing sessions still read.
                assert await sessions.load(record.handle_digest) == record
                assert await sessions.load(successor.handle_digest) is None
            finally:
                async with admin.begin() as connection:
                    await connection.execute(text(repair))
            await sessions.logout(
                record.handle_digest, issuer=record.issuer, subject=record.subject
            )
            assert await sessions.load(record.handle_digest) is None
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("damage", "repair"),
    [
        (
            f"GRANT UPDATE ON {_TABLE} TO {_RUNTIME}",
            (
                f"REVOKE UPDATE ON {_TABLE} FROM {_RUNTIME}",
                f"GRANT UPDATE (generation, fenced_at) ON {_TABLE} TO {_RUNTIME}",
            ),
        ),
        (
            f"REVOKE UPDATE (fenced_at) ON {_TABLE} FROM {_RUNTIME}",
            (f"GRANT UPDATE (fenced_at) ON {_TABLE} TO {_RUNTIME}",),
        ),
        (
            f"REVOKE DELETE ON {_TABLE} FROM {_RUNTIME}",
            (f"GRANT DELETE ON {_TABLE} TO {_RUNTIME}",),
        ),
        (
            f"GRANT SELECT ON {_TABLE} TO PUBLIC",
            (f"REVOKE SELECT ON {_TABLE} FROM PUBLIC",),
        ),
        (
            f"REVOKE UPDATE (current_generation) ON {_GENERATION} FROM {_RUNTIME}",
            (f"GRANT UPDATE (current_generation) ON {_GENERATION} TO {_RUNTIME}",),
        ),
        (
            f"GRANT DELETE ON {_GENERATION} TO {_RUNTIME}",
            (f"REVOKE DELETE ON {_GENERATION} FROM {_RUNTIME}",),
        ),
    ],
    ids=(
        "fence-table-update",
        "fence-instant-update-revoked",
        "fence-delete-revoked",
        "fence-public-select",
        "generation-update-revoked",
        "generation-delete-granted",
    ),
)
def test_fence_acl_drift_fails_the_global_runtime_privilege_attestation(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    damage: str,
    repair: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        sessions = PostgresControlPlaneSessionStore(runtime)
        try:
            start = await sessions.login_start_anchor()
            record = _record("fence-acl", start.started_at)
            await sessions.replace(previous_handle_digest=None, record=record, login_start=start)
            async with admin.begin() as connection:
                await connection.execute(text(damage))
            try:
                with pytest.raises(ControlPlaneSessionStoreUnavailable):
                    await sessions.login_start_anchor()
                # The restricted-principal check covers the whole runtime ACL on every admitted
                # operation, so fence ACL drift also suspends reads of existing sessions.
                with pytest.raises(ControlPlaneSessionStoreUnavailable):
                    await sessions.load(record.handle_digest)
            finally:
                async with admin.begin() as connection:
                    for statement in repair:
                        await connection.execute(text(statement))
            assert await sessions.load(record.handle_digest) == record
            await sessions.login_start_anchor()
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
