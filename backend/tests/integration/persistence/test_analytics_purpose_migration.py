import asyncio

import pytest
from alembic import command
from ci_economics.purpose_configuration_factories import purpose_query
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.analytics_purpose_schema_contract import (
    analytics_purpose_schema_matches,
)
from ci_coordinator.persistence.analytics_purpose_store import TransactionalPurposeSettingsStore
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresPurposeUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.schema_capabilities import ANALYTICS_PURPOSE_SETTINGS

from .conftest import alembic_config

pytestmark = pytest.mark.persistence


def _capabilities(connection: Connection, revision: str) -> set[tuple[str, str]]:
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT capability_id, descriptor_hash FROM "
                "ci_coordinator.database_compatibility_capabilities WHERE revision_id = :revision"
            ),
            {"revision": revision},
        )
    }


def test_forward_0013_is_atomic_preserves_0012_and_requires_new_capability(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, "20260913_0012")
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.connect() as connection:
            prior = _capabilities(connection, "20260913_0012")
        with monkeypatch.context() as patch:
            patch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                "ci-analytics-purpose-settings/v1",
                lambda _connection: False,
            )
            with pytest.raises(RuntimeError, match="ci-analytics-purpose-settings"):
                command.upgrade(config, "20260913_0013")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == "20260913_0012"
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('ci_coordinator.analytics_purpose_settings')")
                )
                is None
            )
            assert _capabilities(connection, "20260913_0012") == prior

        async def rejected_before_query() -> None:
            asynchronous = create_postgres_engine(unmigrated_database_url)
            try:
                with pytest.raises(
                    DatabaseCapabilityUnavailable,
                    match="current database declaration does not provide every required capability",
                ):
                    async with PostgresPurposeUnitOfWork(asynchronous):
                        pytest.fail(
                            "missing purpose capability must reject before repository access"
                        )
            finally:
                await asynchronous.dispose()

        asyncio.run(rejected_before_query())
        command.upgrade(config, "20260913_0013")
        command.upgrade(config, "20260913_0013")
        with engine.connect() as connection:
            assert analytics_purpose_schema_matches(connection)
            added = ANALYTICS_PURPOSE_SETTINGS.declaration()
            assert _capabilities(connection, "20260913_0013") == prior | {
                (added.capability_id, added.descriptor_hash)
            }
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ci_coordinator.analytics_purpose_settings")
                )
                == 0
            )
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, "20260913_0012")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "damage,repair",
    [
        (
            "ALTER COLUMN snapshot_canonical DROP NOT NULL",
            "ALTER COLUMN snapshot_canonical SET NOT NULL",
        ),
        (
            "DROP CONSTRAINT ck_analytics_purpose_payload",
            "ADD CONSTRAINT ck_analytics_purpose_payload CHECK "
            "(octet_length(snapshot_canonical) BETWEEN 1 AND 262144)",
        ),
        (
            "DROP CONSTRAINT ck_analytics_purpose_revision",
            "ADD CONSTRAINT ck_analytics_purpose_revision CHECK "
            "(generation BETWEEN 1 AND 9007199254740991 "
            "AND revision BETWEEN 1 AND 9007199254740991)",
        ),
        (
            "DROP CONSTRAINT fk_analytics_purpose_dataset",
            "ADD CONSTRAINT fk_analytics_purpose_dataset "
            "FOREIGN KEY (installation_id, repository_id) "
            "REFERENCES ci_coordinator.ci_history_datasets "
            "(installation_id, repository_id) ON DELETE RESTRICT",
        ),
        ("ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY"),
    ],
)
def test_native_catalog_rejects_each_damaged_operand(
    postgres_database_url: str, runtime_postgres_database_url: str, damage: str, repair: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            async with PostgresPurposeUnitOfWork(runtime):
                pass
            async with admin.begin() as connection:
                await connection.execute(
                    text(f"ALTER TABLE ci_coordinator.analytics_purpose_settings {damage}")
                )
            try:
                with pytest.raises(
                    DatabaseCapabilityUnavailable,
                    match="analytics purpose schema facts do not match",
                ):
                    async with PostgresPurposeUnitOfWork(runtime):
                        pytest.fail("damaged purpose catalog must reject before repository access")
            finally:
                async with admin.begin() as connection:
                    await connection.execute(
                        text(f"ALTER TABLE ci_coordinator.analytics_purpose_settings {repair}")
                    )
            async with PostgresPurposeUnitOfWork(runtime):
                pass
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "damage,repair",
    [
        (
            "GRANT DELETE ON ci_coordinator.analytics_purpose_settings "
            "TO ci_coordinator_runtime_test",
            (
                "REVOKE DELETE ON ci_coordinator.analytics_purpose_settings "
                "FROM ci_coordinator_runtime_test",
            ),
        ),
        (
            "GRANT UPDATE ON ci_coordinator.analytics_purpose_settings "
            "TO ci_coordinator_runtime_test",
            (
                "REVOKE UPDATE ON ci_coordinator.analytics_purpose_settings "
                "FROM ci_coordinator_runtime_test",
                "GRANT UPDATE (generation, revision, snapshot_canonical) "
                "ON ci_coordinator.analytics_purpose_settings TO ci_coordinator_runtime_test",
            ),
        ),
        (
            "REVOKE INSERT ON ci_coordinator.analytics_purpose_settings "
            "FROM ci_coordinator_runtime_test",
            (
                "GRANT INSERT ON ci_coordinator.analytics_purpose_settings "
                "TO ci_coordinator_runtime_test",
            ),
        ),
        (
            "GRANT SELECT ON ci_coordinator.analytics_purpose_settings TO PUBLIC",
            ("REVOKE SELECT ON ci_coordinator.analytics_purpose_settings FROM PUBLIC",),
        ),
    ],
)
def test_current_runtime_rejects_missing_or_excess_acl(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    damage: str,
    repair: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(runtime))
        try:
            await store.read_settings(purpose_query())
            async with admin.begin() as connection:
                await connection.execute(text(damage))
            try:
                with pytest.raises(CiEconomicsStoreUnavailable):
                    await store.read_settings(purpose_query())
            finally:
                async with admin.begin() as connection:
                    for statement in repair:
                        await connection.execute(text(statement))
            await store.read_settings(purpose_query())
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
