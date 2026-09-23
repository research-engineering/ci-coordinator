from datetime import datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Connection
from tests.integration.persistence._ci_economics_support import provider_source
from tests.integration.persistence.conftest import alembic_config

from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.archive_retention import DetailRetentionPolicy
from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.ci_economics_v4_schema_contract import V4_CATALOG
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_defaults
from ci_coordinator.persistence.ci_history_schema_attestation import (
    ci_history_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_history_schema_contract import HISTORY_CATALOG
from ci_coordinator.persistence.schema import ci_history_defaults, ci_workflow_attempt_collections

pytestmark = pytest.mark.persistence
_PREDECESSOR = "20260910_0010"
_SUCCESSOR = "20260912_0011"
_CAPABILITY = "ci-actions-history-archive/v1"


def test_history_migration_preserves_active_evidence_and_rolls_back_failed_attestation(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            now = connection.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            source = provider_source(773, now - timedelta(seconds=1))
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
            original = tuple(connection.execute(select(ci_workflow_attempt_collections)))
            previous_capabilities = _capabilities(connection, _PREDECESSOR)

        def reject(_connection: Connection) -> bool:
            raise RuntimeError("injected archive admission failure")

        with monkeypatch.context() as patch:
            patch.setitem(migration_result_attestation._CAPABILITY_ATTESTORS, _CAPABILITY, reject)
            with pytest.raises(RuntimeError, match="injected archive admission failure"):
                command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == (
                _PREDECESSOR
            )
            assert tuple(connection.execute(select(ci_workflow_attempt_collections))) == original
            assert not _capabilities(connection, _SUCCESSOR)
            for relation in HISTORY_CATALOG.relations:
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
            assert ci_history_schema_matches_contract_sync(connection)
            assert ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)
            assert tuple(connection.execute(select(ci_workflow_attempt_collections))) == original
            defaults = decode_history_defaults(
                connection.execute(select(ci_history_defaults)).mappings().one()
            )
            assert (defaults.revision, defaults.detail_retention) == (
                1,
                DetailRetentionPolicy("days", 365),
            )
            for name in (
                "datasets",
                "scans",
                "attempts",
                "jobs",
                "details",
                "gaps",
                "rechecks",
                "delivery_inbox",
            ):
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM ci_coordinator.ci_history_{name}")
                    )
                    == 0
                )
            assert _capabilities(connection, _SUCCESSOR) == previous_capabilities | {
                (_CAPABILITY, "ca8c5fee9cb41a354d782619873ec107de4ae57e96835e35970c4ece9e531094")
            }
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _PREDECESSOR)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE ci_coordinator.ci_history_attempts ALTER COLUMN head_sha DROP NOT NULL",
        "ALTER TABLE ci_coordinator.ci_history_attempts "
        "DROP CONSTRAINT ck_ci_history_attempts_detail",
        "ALTER TABLE ci_coordinator.ci_history_jobs DROP CONSTRAINT fk_ci_history_jobs_attempt",
        "ALTER TABLE ci_coordinator.ci_history_scans "
        "ALTER COLUMN next_attempt_at SET DEFAULT now()",
        "ALTER TABLE ci_coordinator.ci_history_details ADD COLUMN unexpected_authority boolean",
        "ALTER TABLE ci_coordinator.ci_history_datasets ENABLE ROW LEVEL SECURITY",
        "DROP INDEX ci_coordinator.ix_ci_history_scans_due",
        "DROP INDEX ci_coordinator.ix_ci_history_rechecks_due",
        "DROP INDEX ci_coordinator.ix_ci_history_delivery_inbox_pending",
        "DROP INDEX ci_coordinator.ix_ci_history_delivery_inbox_workflow",
        "ALTER TABLE ci_coordinator.ci_history_delivery_inbox "
        "DROP CONSTRAINT fk_ci_history_delivery_inbox_source",
        "ALTER TABLE ci_coordinator.ci_history_delivery_inbox "
        "DROP CONSTRAINT ck_ci_history_delivery_inbox_receipt",
        "ALTER TABLE ci_coordinator.ci_history_delivery_inbox "
        "ALTER COLUMN delivered_generation SET DEFAULT 0",
        "ALTER TABLE ci_coordinator.ci_history_rechecks "
        "DROP CONSTRAINT ck_ci_history_rechecks_lease",
        "ALTER TABLE ci_coordinator.ci_history_rechecks "
        "DROP CONSTRAINT ck_ci_history_rechecks_attempts",
        "CREATE INDEX unexpected_history_index ON ci_coordinator.ci_history_scans (revision)",
        "CREATE TRIGGER unexpected_history_trigger BEFORE UPDATE ON "
        "ci_coordinator.ci_history_scans FOR EACH ROW EXECUTE FUNCTION "
        "ci_coordinator.guard_ci_economics_mutation()",
        "DELETE FROM ci_coordinator.ci_history_defaults",
        "UPDATE ci_coordinator.ci_history_defaults SET detail_policy_canonical = '{}'::bytea",
    ],
)
def test_history_admission_rejects_catalog_or_default_drift(
    postgres_database_url: str, statement: str
) -> None:
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            assert ci_history_schema_matches_contract_sync(connection)
            connection.execute(text(statement))
            assert not ci_history_schema_matches_contract_sync(connection)
            assert ci_economics_schema_matches_contract_sync(connection, contract=V4_CATALOG)
            connection.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("policy", [b'{"mode":"disabled"}', b'{"mode":"forever"}'])
def test_history_admission_accepts_valid_noninitial_defaults(
    postgres_database_url: str, policy: bytes
) -> None:
    engine = create_engine(postgres_database_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                ci_history_defaults.update().values(revision=2, detail_policy_canonical=policy)
            )
            assert ci_history_schema_matches_contract_sync(connection)
            connection.rollback()
    finally:
        engine.dispose()


def _capabilities(connection: Connection, revision: str) -> set[tuple[str, str]]:
    return {
        (row.capability_id, row.descriptor_hash)
        for row in connection.execute(
            text(
                "SELECT capability_id, descriptor_hash FROM "
                "ci_coordinator.database_compatibility_capabilities WHERE revision_id = :revision"
            ),
            {"revision": revision},
        )
    }
