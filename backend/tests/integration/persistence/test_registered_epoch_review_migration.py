"""Native migration, catalog and ACL witnesses for registered-epoch-review/v1."""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.proposal_review_unit_of_work import (
    PostgresProposalReviewUnitOfWork,
)
from ci_coordinator.persistence.registered_epoch_review_schema_contract import (
    registered_epoch_review_schema_matches_sync,
)
from ci_coordinator.persistence.registered_epoch_review_unit_of_work import (
    PostgresRegisteredEpochReviewUnitOfWork,
)
from ci_coordinator.persistence.schema_capabilities import REGISTERED_EPOCH_REVIEW

from .conftest import alembic_config

pytestmark = pytest.mark.persistence

_PREDECESSOR = "20261011_0018"
_SUCCESSOR = "20261011_0019"
_PENDING = "ci_coordinator.registered_epoch_attestation_transactions"
_REVIEWS = "ci_coordinator.registered_epoch_reviews"


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


def test_forward_0019_is_atomic_preserves_0018_and_adds_only_registered_review(
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
                REGISTERED_EPOCH_REVIEW.capability_id,
                lambda _connection: False,
            )
            with pytest.raises(RuntimeError, match="registered-epoch-review"):
                command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == _PREDECESSOR
            )
            assert connection.scalar(text(f"SELECT to_regclass('{_PENDING}')")) is None
            assert connection.scalar(text(f"SELECT to_regclass('{_REVIEWS}')")) is None
            assert _capabilities(connection, _PREDECESSOR) == prior
            assert _capabilities(connection, _SUCCESSOR) == set()
        command.upgrade(config, _SUCCESSOR)
        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert registered_epoch_review_schema_matches_sync(connection)
            added = REGISTERED_EPOCH_REVIEW.declaration()
            assert _capabilities(connection, _SUCCESSOR) == prior | {
                (added.capability_id, added.descriptor_hash)
            }
            for table in (_PENDING, _REVIEWS):
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('public', :table, 'SELECT')"),
                        {"table": table},
                    )
                    is False
                )
        with pytest.raises(RuntimeError, match="forward repair"):
            command.downgrade(config, _PREDECESSOR)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("damage", "repair"),
    [
        (
            f"ALTER TABLE {_REVIEWS} DISABLE TRIGGER tr_registered_epoch_reviews_immutable",
            f"ALTER TABLE {_REVIEWS} ENABLE TRIGGER tr_registered_epoch_reviews_immutable",
        ),
        (
            f"ALTER TABLE {_REVIEWS} DROP CONSTRAINT fk_registered_epoch_reviews_registration",
            f"ALTER TABLE {_REVIEWS} ADD CONSTRAINT fk_registered_epoch_reviews_registration "
            "FOREIGN KEY (installation_id, repository_id, registration_operation_id) "
            "REFERENCES ci_coordinator.config_epoch_registrations "
            "(installation_id, repository_id, operation_id) ON DELETE RESTRICT",
        ),
        (
            f"ALTER TABLE {_PENDING} DROP CONSTRAINT "
            "ck_registered_epoch_attestation_transactions_subject",
            f"ALTER TABLE {_PENDING} ADD CONSTRAINT "
            "ck_registered_epoch_attestation_transactions_subject CHECK ("
            "target_epoch_id ~ '^[0-9a-f]{64}$' AND source_hash ~ '^[0-9a-f]{64}$' "
            "AND provider_revision ~ '^[0-9a-f]{40}$' AND inventory_digest ~ '^[0-9a-f]{64}$')",
        ),
    ],
    ids=("trigger-disabled", "registration-fk-dropped", "subject-check-dropped"),
)
def test_damaged_registered_catalog_fails_closed_only_for_its_subject(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    damage: str,
    repair: str,
) -> None:
    owner = create_engine(postgres_database_url)

    async def attempt() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            with pytest.raises(DatabaseCapabilityUnavailable, match="registered-epoch review"):
                async with PostgresRegisteredEpochReviewUnitOfWork(runtime):
                    pytest.fail("damaged registered-epoch catalog exposed its repository")
            async with PostgresProposalReviewUnitOfWork(runtime) as transaction:
                await transaction.rollback()
        finally:
            await runtime.dispose()

    try:
        with owner.begin() as connection:
            assert registered_epoch_review_schema_matches_sync(connection)
            connection.execute(text(damage))
            assert not registered_epoch_review_schema_matches_sync(connection)
        asyncio.run(attempt())
    finally:
        with owner.begin() as connection:
            connection.execute(text(repair))
            assert registered_epoch_review_schema_matches_sync(connection)
        owner.dispose()
