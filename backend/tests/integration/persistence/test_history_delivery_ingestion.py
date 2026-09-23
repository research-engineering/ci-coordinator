import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryDuplicate,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.persistence import PostgresWebhookIngestionUnitOfWork
from ci_coordinator.persistence.ci_history_delivery_codec import (
    decode_history_delivery,
    history_delivery_fields,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_history_delivery_inbox,
    ci_workflow_observations,
    webhook_deliveries,
)

from ._ci_economics_support import database_now
from ._workflow_observation_support import ingest_workflow_observation, workflow_observation

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("cut", ["commit", "rollback", "source_insert", "inbox_insert"])
def test_ingestion_commits_all_four_records_or_none(
    runtime_postgres_database_url: str,
    cut: Literal["commit", "rollback", "source_insert", "inbox_insert"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        rejected = False

        def reject(_connection: object, _cursor: object, statement: str, *_args: object) -> None:
            nonlocal rejected
            table = (
                "ci_workflow_observations"
                if cut == "source_insert"
                else "ci_history_delivery_inbox"
            )
            if statement.startswith(f"INSERT INTO ci_coordinator.{table} "):
                rejected = True
                raise SQLAlchemyError("injected ingestion crash cut")

        try:
            observation = workflow_observation("atomic-delivery", await database_now(engine))
            if cut in {"source_insert", "inbox_insert"}:
                event.listen(engine.sync_engine, "after_cursor_execute", reject)
            try:
                async with PostgresWebhookIngestionUnitOfWork(engine) as transaction:
                    result = await transaction.webhook_ingestion.commit(
                        prepare_delivery_claim(observation.provenance), observation
                    )
                    if cut in {"commit", "rollback"}:
                        assert isinstance(result, DeliveryClaimed)
                    else:
                        assert rejected and isinstance(result, DeliveryStoreUnavailable)
                    if cut == "commit":
                        await transaction.commit()
            finally:
                if cut in {"source_insert", "inbox_insert"}:
                    event.remove(engine.sync_engine, "after_cursor_execute", reject)
            async with engine.connect() as connection:
                for table in (
                    webhook_deliveries,
                    audit_events,
                    ci_workflow_observations,
                    ci_history_delivery_inbox,
                ):
                    assert await connection.scalar(select(func.count()).select_from(table)) == (
                        1 if cut == "commit" else 0
                    )
                if cut == "commit":
                    source = (
                        (await connection.execute(select(ci_workflow_observations)))
                        .mappings()
                        .one()
                    )
                    inbox = (
                        (await connection.execute(select(ci_history_delivery_inbox)))
                        .mappings()
                        .one()
                    )
                    assert dict(inbox) == {
                        **history_delivery_fields(decode_history_delivery(source)),
                        "delivered_generation": 0,
                        "delivered_at": None,
                    }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("alias", [False, True])
def test_ingestion_replay_preserves_canonical_source_and_receipt(
    runtime_postgres_database_url: str, alias: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            observation = workflow_observation("original", await database_now(engine))
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            async with engine.begin() as connection:
                await connection.execute(
                    update(ci_history_delivery_inbox).values(
                        delivered_generation=1, delivered_at=func.statement_timestamp()
                    )
                )
                source = (
                    (await connection.execute(select(ci_workflow_observations))).mappings().one()
                )
                receipt = (
                    (await connection.execute(select(ci_history_delivery_inbox))).mappings().one()
                )
            if alias:
                observation = replace(
                    observation, provenance=replace(observation.provenance, delivery_id="alias")
                )
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryDuplicate
            )
            async with engine.connect() as connection:
                assert (
                    await connection.execute(select(ci_workflow_observations))
                ).mappings().one() == source
                assert (
                    (await connection.execute(select(ci_history_delivery_inbox))).mappings().one()
                ) == receipt
                assert (
                    await connection.scalar(select(func.count()).select_from(webhook_deliveries))
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unknown_workflow_does_not_invent_a_history_hint(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            observation = replace(
                workflow_observation("unknown-workflow", await database_now(engine)),
                workflow_id=None,
            )
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_observations)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_history_delivery_inbox)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
