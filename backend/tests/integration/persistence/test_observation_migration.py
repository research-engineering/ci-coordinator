from datetime import datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Connection
from tests.integration.persistence._ci_economics_support import provider_source
from tests.integration.persistence.conftest import alembic_config

from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.ci_economics_v4_schema_contract import V4_CATALOG
from ci_coordinator.persistence.ci_observation_schema_contract import OBSERVATION_CATALOG
from ci_coordinator.persistence.schema import (
    ci_observation_gaps,
    ci_observation_scans,
    ci_observation_subscriptions,
    ci_workflow_attempt_collections,
)

pytestmark = pytest.mark.persistence
_PREDECESSOR = "20260909_0009"
_SUCCESSOR = "20260910_0010"


def test_observation_upgrade_is_atomic_additive_and_does_not_enable_collection(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            now = connection.scalar(select(func.statement_timestamp()))
            assert isinstance(now, datetime)
            source = provider_source(772, now - timedelta(seconds=1))
            state = initial_collection_state(
                source.source_id,
                source.run_created_at,
                now,
                load_bundled_ci_economics_profile().collection_policy,
            )
            connection.execute(
                ci_workflow_attempt_collections.insert().values(
                    encode_collection_record(state, source)
                )
            )
            before = tuple(connection.execute(select(ci_workflow_attempt_collections)))
            declarations = tuple(
                connection.execute(
                    text(
                        "SELECT * FROM ci_coordinator.database_compatibility_declarations "
                        "ORDER BY generation"
                    )
                )
            )
            assert ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)

        def reject(_connection: Connection) -> bool:
            raise RuntimeError("injected observation admission failure")

        with monkeypatch.context() as patch:
            patch.setitem(
                migration_result_attestation._CAPABILITY_ATTESTORS,
                "ci-repository-observation/v1",
                reject,
            )
            with pytest.raises(RuntimeError, match="injected observation admission failure"):
                command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _PREDECESSOR
            )
            assert tuple(connection.execute(select(ci_workflow_attempt_collections))) == before
            assert (
                tuple(
                    connection.execute(
                        text(
                            "SELECT * FROM ci_coordinator.database_compatibility_declarations "
                            "ORDER BY generation"
                        )
                    )
                )
                == declarations
            )
            for relation in OBSERVATION_CATALOG.relations:
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:relation)"),
                        {"relation": f"ci_coordinator.{relation}"},
                    )
                    is None
                )
        command.upgrade(config, _SUCCESSOR)
        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert tuple(connection.execute(select(ci_workflow_attempt_collections))) == before
            assert ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)
            assert ci_economics_schema_matches_contract_sync(
                connection, contract=OBSERVATION_CATALOG
            )
            for table in (ci_observation_subscriptions, ci_observation_scans, ci_observation_gaps):
                assert connection.scalar(select(func.count()).select_from(table)) == 0
            predecessor = tuple(
                connection.execute(
                    text(
                        "SELECT capability_id, descriptor_hash FROM "
                        "ci_coordinator.database_compatibility_capabilities "
                        "WHERE revision_id = :revision "
                        "ORDER BY capability_id"
                    ),
                    {"revision": _PREDECESSOR},
                )
            )
            successor = tuple(
                connection.execute(
                    text(
                        "SELECT capability_id, descriptor_hash FROM "
                        "ci_coordinator.database_compatibility_capabilities "
                        "WHERE revision_id = :revision "
                        "ORDER BY capability_id"
                    ),
                    {"revision": _SUCCESSOR},
                )
            )
            assert set(predecessor) < set(successor)
            assert set(successor) - set(predecessor) == {
                (
                    "ci-repository-observation/v1",
                    "9bc5774fc83c29990eb49c224e7587320fe75fd4b27665cd50415a0d8b1c8ed2",
                )
            }
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _PREDECESSOR)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE ci_coordinator.ci_observation_subscriptions "
        "ALTER COLUMN snapshot_digest DROP NOT NULL",
        "ALTER TABLE ci_coordinator.ci_observation_subscriptions "
        "DROP CONSTRAINT ck_ci_observation_subscriptions_payload",
        "ALTER TABLE ci_coordinator.ci_observation_scans "
        "DROP CONSTRAINT fk_ci_observation_scans_subscription",
        "ALTER TABLE ci_coordinator.ci_observation_scans "
        "ALTER COLUMN next_attempt_at SET DEFAULT now()",
        "ALTER TABLE ci_coordinator.ci_observation_gaps ADD COLUMN unrelated_decision boolean",
        "ALTER TABLE ci_coordinator.ci_observation_gaps ENABLE ROW LEVEL SECURITY",
        "DROP INDEX ci_coordinator.ix_ci_observation_gaps_scope",
        "CREATE INDEX unexpected_observation_index ON "
        "ci_coordinator.ci_observation_scans (revision)",
        "CREATE TRIGGER unexpected_observation_trigger BEFORE UPDATE ON "
        "ci_coordinator.ci_observation_subscriptions FOR EACH ROW EXECUTE FUNCTION "
        "ci_coordinator.guard_ci_economics_mutation()",
    ],
)
def test_observation_catalog_rejects_changed_semantics_without_disturbing_old_economics(
    postgres_database_url: str, statement: str
) -> None:
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert ci_economics_schema_matches_contract_sync(
                connection, contract=OBSERVATION_CATALOG
            )
            connection.execute(text(statement))
            assert not ci_economics_schema_matches_contract_sync(
                connection, contract=OBSERVATION_CATALOG
            )
            assert ci_economics_schema_matches_contract_sync(connection, contract=V4_CATALOG)
            connection.rollback()
    finally:
        engine.dispose()
