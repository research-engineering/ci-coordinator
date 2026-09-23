import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection
from tests.integration.persistence.conftest import alembic_config

from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.activity_schema_attestation import (
    activity_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_history_schema_attestation import (
    ci_history_schema_matches_contract_sync,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_history_defaults
from ci_coordinator.persistence.schema_capabilities import ADMINISTRATOR_ACTIVITY

pytestmark = pytest.mark.persistence


def test_forward_activity_migration_is_atomic_and_preserves_archive(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, "20260912_0011")
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.connect() as connection:
            original = tuple(connection.execute(select(ci_history_defaults)))
            prior = _capabilities(connection, "20260912_0011")

        def reject(_connection: Connection) -> bool:
            return False

        with monkeypatch.context() as patch:
            patch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                "administrator-activity/v1",
                reject,
            )
            with pytest.raises(RuntimeError, match="administrator-activity"):
                command.upgrade(config, "20260913_0012")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == "20260912_0011"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('ci_coordinator.activity_events')"))
                is None
            )
            assert not _capabilities(connection, "20260913_0012")
            assert tuple(connection.execute(select(ci_history_defaults))) == original

        async def reject_before_activity_sql() -> None:
            asynchronous = create_postgres_engine(unmigrated_database_url)
            try:
                with pytest.raises(ActivityUnavailable):
                    await PostgresActivityStore(
                        asynchronous, ActivityCursorCodec(b"a" * 32)
                    ).cleanup()
            finally:
                await asynchronous.dispose()

        asyncio.run(reject_before_activity_sql())
        command.upgrade(config, "20260913_0012")
        command.upgrade(config, "20260913_0012")
        with engine.connect() as connection:
            assert activity_schema_matches_contract_sync(connection)
            assert ci_history_schema_matches_contract_sync(connection)
            assert tuple(connection.execute(select(ci_history_defaults))) == original
            declaration = ADMINISTRATOR_ACTIVITY.declaration()
            assert _capabilities(connection, "20260913_0012") == prior | {
                (declaration.capability_id, declaration.descriptor_hash)
            }
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, "20260912_0011")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "damage",
    [
        "ALTER TABLE ci_coordinator.activity_events ALTER COLUMN issuer DROP NOT NULL",
        "ALTER TABLE ci_coordinator.activity_events DROP CONSTRAINT ck_activity_outcome",
        "DROP INDEX ci_coordinator.ix_activity_issuer_sequence",
        "DROP INDEX ci_coordinator.ix_activity_retention",
        "ALTER TABLE ci_coordinator.activity_events ALTER COLUMN sequence SET GENERATED ALWAYS",
        "ALTER SEQUENCE ci_coordinator.activity_events_sequence_seq INCREMENT BY 2",
        "ALTER SEQUENCE ci_coordinator.activity_events_sequence_seq CACHE 2",
        "ALTER TABLE ci_coordinator.activity_diagnostic_buckets "
        "DROP CONSTRAINT ck_activity_diagnostic_bucket",
        "ALTER TABLE ci_coordinator.activity_events ENABLE ROW LEVEL SECURITY",
    ],
)
def test_independent_activity_catalog_rejects_each_mutated_operand(
    postgres_database_url: str, damage: str
) -> None:
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert activity_schema_matches_contract_sync(connection)
            connection.execute(text(damage))
            assert not activity_schema_matches_contract_sync(connection)
            assert ci_history_schema_matches_contract_sync(connection)
            connection.rollback()
            assert activity_schema_matches_contract_sync(connection)
    finally:
        engine.dispose()


def _capabilities(connection: Connection, revision: str) -> set[tuple[str, str]]:
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT capability_id, descriptor_hash FROM "
                "ci_coordinator.database_compatibility_capabilities "
                "WHERE revision_id = :revision"
            ),
            {"revision": revision},
        )
    }
