import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryDuplicate,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.persistence import PostgresWebhookIngestionUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.workflow_observation_lock import lock_workflow_observation

from ._ci_economics_support import database_now
from ._workflow_observation_support import ingest_workflow_observation, workflow_observation

pytestmark = pytest.mark.persistence


def test_archive_guard_refusal_releases_audit_head_for_an_independent_delivery(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            now = await database_now(engine)
            original = workflow_observation("held-history-delivery", now)
            third = workflow_observation("independent-history-delivery", now)
            assert isinstance(await ingest_workflow_observation(engine, original), DeliveryClaimed)
            async with engine.begin() as archive:
                await lock_workflow_observation(archive, original.provenance.delivery_id)
                async with asyncio.timeout(2):
                    async with PostgresWebhookIngestionUnitOfWork(engine) as duplicate:
                        outcome = await duplicate.webhook_ingestion.commit(
                            prepare_delivery_claim(original.provenance), original
                        )
                        assert isinstance(outcome, DeliveryStoreUnavailable)
                        await duplicate.rollback()
                    assert isinstance(
                        await ingest_workflow_observation(engine, third), DeliveryClaimed
                    )
            assert isinstance(
                await ingest_workflow_observation(engine, original), DeliveryDuplicate
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "relation,privilege,expected",
    [
        ("ci_history_attempts", "DELETE", False),
        ("ci_history_jobs", "DELETE", False),
        ("ci_history_gaps", "DELETE", False),
        ("ci_history_defaults", "UPDATE", False),
        ("ci_history_details", "DELETE", True),
        ("ci_history_rechecks", "DELETE", True),
    ],
)
def test_archive_runtime_grants_only_current_lifecycle_writers(
    runtime_postgres_database_url: str, relation: str, privilege: str, expected: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with engine.connect() as connection:
                value = await connection.scalar(
                    text("SELECT has_table_privilege(:relation, :privilege)"),
                    {"relation": "ci_coordinator." + relation, "privilege": privilege},
                )
                assert value is expected
                if relation == "ci_history_defaults":
                    assert (
                        await connection.scalar(
                            text("SELECT has_any_column_privilege(:relation, 'UPDATE')"),
                            {"relation": "ci_coordinator." + relation},
                        )
                        is False
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
