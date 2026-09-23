import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryDuplicate,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.persistence import PostgresWebhookIngestionUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    audit_ledger_head,
    ci_history_delivery_inbox,
    ci_workflow_observations,
    webhook_deliveries,
)
from ci_coordinator.persistence.workflow_observation_lock import lock_workflow_observation

from ._ci_economics_support import database_now
from ._workflow_observation_support import workflow_observation

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("operation", ["insert", "replay", "alias"])
def test_ingestion_guards_canonical_source_before_insertion_or_duplicate_admission(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    operation: Literal["insert", "replay", "alias"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        observer = create_postgres_engine(postgres_database_url)
        try:
            observation = workflow_observation("canonical-delivery", await database_now(observer))
            original_expiry = None
            if operation != "insert":
                async with PostgresWebhookIngestionUnitOfWork(engine) as transaction:
                    assert isinstance(
                        await transaction.webhook_ingestion.commit(
                            prepare_delivery_claim(observation.provenance), observation
                        ),
                        DeliveryClaimed,
                    )
                    await transaction.commit()
                async with engine.connect() as connection:
                    original_expiry = await connection.scalar(
                        select(ci_workflow_observations.c.retain_until)
                    )
            if operation == "alias":
                observation = replace(
                    observation,
                    provenance=replace(observation.provenance, delivery_id="incoming-alias"),
                )

            async def ingest(*, allowed: bool) -> None:
                async with PostgresWebhookIngestionUnitOfWork(engine) as transaction:
                    result = await transaction.webhook_ingestion.commit(
                        prepare_delivery_claim(observation.provenance), observation
                    )
                    expected = (
                        (DeliveryClaimed if operation == "insert" else DeliveryDuplicate)
                        if allowed
                        else DeliveryStoreUnavailable
                    )
                    assert isinstance(result, expected)
                    if allowed:
                        await transaction.commit()
                    else:
                        with pytest.raises(RuntimeError, match="unit of work is not active"):
                            await transaction.commit()

            async with engine.connect() as holder, asyncio.timeout(15):
                await lock_workflow_observation(holder, "canonical-delivery")
                async with observer.connect() as check:
                    before = await _pair_snapshot(check)
                async with asyncio.timeout(2):
                    await ingest(allowed=False)
                async with observer.connect() as check:
                    assert await _pair_snapshot(check) == before
                    assert await check.scalar(
                        select(func.count()).select_from(ci_workflow_observations)
                    ) == int(operation != "insert")
                await holder.commit()
            await ingest(allowed=True)
            async with engine.connect() as connection:
                rows = (await connection.execute(select(ci_workflow_observations))).mappings().all()
                assert len(rows) == 1
                assert rows[0]["delivery_id"] == "canonical-delivery"
                if original_expiry is not None:
                    assert rows[0]["retain_until"] == original_expiry
        finally:
            await observer.dispose()
            await engine.dispose()

    asyncio.run(scenario())


async def _pair_snapshot(connection: AsyncConnection) -> tuple[tuple[object, ...], ...]:
    tables = (
        audit_events,
        audit_ledger_head,
        webhook_deliveries,
        ci_workflow_observations,
        ci_history_delivery_inbox,
    )
    states = [tuple(await connection.execute(select(table))) for table in tables]
    return tuple(states)
