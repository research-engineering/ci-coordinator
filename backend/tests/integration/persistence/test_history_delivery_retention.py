import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError

from ci_coordinator.ci_economics.history_commands import HistoryConfigured
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckClaim
from ci_coordinator.github_ingestion import DeliveryClaimed, DeliveryDuplicate
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_history_delivery_codec import (
    decode_history_delivery,
    history_delivery_fields,
)
from ci_coordinator.persistence.ci_history_delivery_transfer import transfer_history_deliveries
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_delivery_inbox,
    ci_history_jobs,
    ci_history_rechecks,
    ci_workflow_observations,
)
from ci_coordinator.persistence.workflow_observation_lock import lock_workflow_observation

from ._ci_economics_support import database_now
from ._history_support import history_command, history_store
from ._workflow_observation_support import (
    ingest_workflow_observation,
    seed_workflow_observation,
    workflow_observation,
)

pytestmark = pytest.mark.persistence


def test_source_expiry_cascades_only_transient_receipt_and_replay_binds_new_source_epoch(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            observation = replace(
                workflow_observation("restored-delivery", ARCHIVE_TIME + timedelta(minutes=5)),
                workflow_run_id=303,
                workflow_id=404,
                head_sha="a" * 40,
                created_at=ARCHIVE_TIME,
            )
            now = await database_now(admin)
            async with admin.begin() as connection:
                await lock_workflow_observation(connection, observation.provenance.delivery_id)
                await seed_workflow_observation(connection, observation, now - timedelta(seconds=1))
                row = (await connection.execute(select(ci_workflow_observations))).mappings().one()
                source = decode_history_delivery(row)
                await connection.execute(
                    insert(ci_history_delivery_inbox).values(
                        **history_delivery_fields(source),
                        delivered_generation=1,
                        delivered_at=source.recorded_at + timedelta(seconds=1),
                    )
                )
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            assert source.hint is not None
            assert await store.enqueue_recheck(source.hint) == "admitted"
            claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(claim, HistoryRecheckClaim)
            assert await store.record_recheck_statistics(claim, archived_statistics()) == "applied"
            async with engine.connect() as connection:
                archived = (await connection.execute(select(ci_history_attempts))).mappings().one()
            transferred = await store.transfer_deliveries(command.scope)
            assert transferred.status == "empty" and transferred.expired_pending_sample == 0
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert await transaction.ci_economics.delete_expired_observations(limit=1) == 1
                await transaction.commit()
            async with engine.connect() as connection:
                for table in (ci_workflow_observations, ci_history_delivery_inbox):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
                assert (
                    await connection.execute(select(ci_history_attempts))
                ).mappings().one() == archived
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_jobs)) == 1
                )
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryDuplicate
            )
            async with engine.connect() as connection:
                fresh = (
                    (await connection.execute(select(ci_history_delivery_inbox))).mappings().one()
                )
                assert fresh["source_fingerprint"] != source.source_fingerprint
                assert fresh["source_recorded_at"] >= now
                assert fresh["source_retain_until"] == fresh["source_recorded_at"] + timedelta(
                    days=90
                )
                assert fresh["delivered_generation"] == 0 and fresh["delivered_at"] is None
            assert (await store.transfer_deliveries(command.scope)).transferred_count == 1
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("acknowledged", [False, True])
def test_expired_pending_source_is_observable_without_transfer_and_cleanup_ends_the_sample(
    runtime_postgres_database_url: str, postgres_database_url: str, acknowledged: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store, command = history_store(engine), history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            assert (await store.transfer_deliveries(command.scope)).expired_pending_sample == 0
            observation = workflow_observation(
                "expired-pending", ARCHIVE_TIME + timedelta(minutes=5)
            )
            now = await database_now(admin)
            async with admin.begin() as connection:
                await seed_workflow_observation(connection, observation, now - timedelta(seconds=1))
                row = (await connection.execute(select(ci_workflow_observations))).mappings().one()
                source = decode_history_delivery(row)
                await connection.execute(
                    insert(ci_history_delivery_inbox).values(
                        **history_delivery_fields(source),
                        delivered_generation=int(acknowledged),
                        delivered_at=source.recorded_at + timedelta(seconds=1)
                        if acknowledged
                        else None,
                    )
                )
            for _ in range(2):
                result = await store.transfer_deliveries(command.scope)
                assert (
                    result.status == "empty"
                    and result.candidate_count == result.transferred_count == 0
                )
                assert result.expired_pending_sample == int(not acknowledged)
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
                assert await connection.scalar(
                    select(ci_history_delivery_inbox.c.delivered_generation)
                ) == int(acknowledged)
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                assert await transaction.ci_economics.delete_expired_observations(limit=1) == 1
                await transaction.commit()
            assert (await store.transfer_deliveries(command.scope)).expired_pending_sample == 0
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_bounded_expiry_probe_does_not_starve_eligible_transfer(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store, command = history_store(engine), history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            now = await database_now(admin)
            async with admin.begin() as connection:
                for index in range(3):
                    observation = workflow_observation(f"expired-{index}", ARCHIVE_TIME)
                    await seed_workflow_observation(
                        connection, observation, now - timedelta(seconds=1)
                    )
                    row = (
                        (
                            await connection.execute(
                                select(ci_workflow_observations).where(
                                    ci_workflow_observations.c.delivery_id
                                    == observation.provenance.delivery_id
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    await connection.execute(
                        insert(ci_history_delivery_inbox).values(
                            **history_delivery_fields(decode_history_delivery(row)),
                            delivered_generation=0,
                            delivered_at=None,
                        )
                    )
            async with engine.begin() as connection:
                result = await transfer_history_deliveries(connection, command.scope, limit=1)
                assert result.expired_pending_sample == 1 and result.transferred_count == 0
            assert (await store.transfer_deliveries(command.scope)).expired_pending_sample == 3
            observation = workflow_observation("eligible-after-expiry", now)
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            async with engine.begin() as connection:
                result = await transfer_history_deliveries(connection, command.scope, limit=1)
                assert result.transferred_count == result.candidate_count == 1
                assert result.expired_pending_sample in {0, 1}
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 1
                )
                receipts = (
                    (
                        await connection.execute(
                            select(ci_history_delivery_inbox.c.delivered_generation)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert sorted(receipts) == [0, 0, 0, 1]
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM ci_coordinator.ci_history_delivery_inbox",
        "UPDATE ci_coordinator.ci_history_delivery_inbox "
        "SET source_fingerprint = source_fingerprint",
        "UPDATE ci_coordinator.ci_history_delivery_inbox SET workflow_id = workflow_id",
        "UPDATE ci_coordinator.ci_history_delivery_inbox "
        "SET source_recorded_at = source_recorded_at",
        "UPDATE ci_coordinator.ci_workflow_observations SET retain_until = retain_until",
    ],
)
def test_runtime_cannot_rewrite_source_or_delete_its_delivered_receipt(
    runtime_postgres_database_url: str, statement: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            observation = workflow_observation("protected-delivery", await database_now(engine))
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            async with engine.connect() as connection:
                with pytest.raises(DBAPIError) as failure:
                    await connection.execute(text(statement))
                assert getattr(failure.value.orig, "sqlstate", None) == "42501"
                await connection.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
