import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.ci_economics.history_commands import HistoryConfigured
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckClaim
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryDuplicate,
    DeliveryIdempotencyResult,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.events import NormalizedWorkflowRunEvent
from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.persistence import (
    PostgresCiEconomicsUnitOfWork,
    PostgresWebhookIngestionUnitOfWork,
)
from ci_coordinator.persistence import ci_economics_repository as economics_storage
from ci_coordinator.persistence import runtime_delivery_repository as delivery_storage
from ci_coordinator.persistence.ci_history_delivery_codec import (
    decode_history_delivery,
    history_delivery_fields,
)
from ci_coordinator.persistence.ci_history_delivery_ingress import ensure_history_delivery_inbox
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_delivery_inbox,
    ci_history_jobs,
    ci_workflow_observations,
)
from ci_coordinator.persistence.workflow_observation_lock import (
    lock_workflow_observation,
    try_lock_workflow_observations,
)

from ._ci_economics_support import database_now
from ._history_support import history_command, history_store
from ._workflow_observation_support import (
    ingest_workflow_observation,
    seed_workflow_observation,
    workflow_observation,
)

pytestmark = pytest.mark.persistence

type RaceOrder = Literal["producer_first", "cleanup_first"]


async def _runtime_ingest(
    engine: AsyncEngine, observation: NormalizedWorkflowRunEvent
) -> DeliveryIdempotencyResult:
    async with PostgresWebhookIngestionUnitOfWork(engine) as transaction:
        result = await transaction.webhook_ingestion.commit(
            prepare_delivery_claim(observation.provenance), observation
        )
        if isinstance(result, DeliveryClaimed | DeliveryDuplicate):
            await transaction.commit()
        else:
            await transaction.rollback()
        return result


async def _cleanup_expired_source(engine: AsyncEngine) -> int:
    async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
        deleted = await transaction.ci_economics.delete_expired_observations(limit=1)
        await transaction.commit()
        return deleted


async def _archive_snapshot(engine: AsyncEngine) -> tuple[tuple[tuple[object, ...], ...], ...]:
    async with engine.connect() as connection:
        attempts = tuple(
            tuple(row)
            for row in await connection.execute(
                select(ci_history_attempts).order_by(*ci_history_attempts.primary_key.columns)
            )
        )
        jobs = tuple(
            tuple(row)
            for row in await connection.execute(
                select(ci_history_jobs).order_by(*ci_history_jobs.primary_key.columns)
            )
        )
    return attempts, jobs


async def _pair_snapshot(
    engine: AsyncEngine, delivery_id: str
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    async with engine.connect() as connection:
        source_rows = (
            (
                await connection.execute(
                    select(ci_workflow_observations).where(
                        ci_workflow_observations.c.delivery_id == delivery_id
                    )
                )
            )
            .mappings()
            .all()
        )
        inbox_rows = (
            (
                await connection.execute(
                    select(ci_history_delivery_inbox).where(
                        ci_history_delivery_inbox.c.delivery_id == delivery_id
                    )
                )
            )
            .mappings()
            .all()
        )
    source_ids = {row["delivery_id"] for row in source_rows}
    inbox_ids = {row["delivery_id"] for row in inbox_rows}
    assert inbox_ids <= source_ids
    if inbox_rows:
        assert len(source_rows) == len(inbox_rows) == 1
        assert dict(inbox_rows[0]) == {
            **history_delivery_fields(decode_history_delivery(source_rows[0])),
            "delivered_generation": 0,
            "delivered_at": None,
        }
    return tuple(source_ids), tuple(inbox_ids)


async def _prepare_expired_source(
    engine: AsyncEngine,
    admin: AsyncEngine,
    order: RaceOrder,
) -> tuple[NormalizedWorkflowRunEvent, tuple[tuple[tuple[object, ...], ...], ...]]:
    command = history_command()
    store = history_store(engine)
    assert isinstance(await store.configure_history(command), HistoryConfigured)

    now = await database_now(admin)
    expired_at = now - timedelta(seconds=1)
    recorded_at = expired_at - timedelta(days=90)
    observation = replace(
        workflow_observation(f"cascade-race-{order}", recorded_at),
        workflow_run_id=303,
        workflow_id=404,
        head_sha="a" * 40,
        created_at=ARCHIVE_TIME,
    )
    async with admin.begin() as connection:
        await lock_workflow_observation(connection, observation.provenance.delivery_id)
        await seed_workflow_observation(connection, observation, expired_at)
    assert isinstance(await ingest_workflow_observation(engine, observation), DeliveryDuplicate)

    async with engine.connect() as connection:
        source_row = (await connection.execute(select(ci_workflow_observations))).mappings().one()
    source = decode_history_delivery(source_row)
    assert source.recorded_at == recorded_at
    assert source.retain_until == expired_at == source.recorded_at + timedelta(days=90)
    assert observation.updated_at is not None and observation.updated_at <= source.recorded_at
    assert source.hint is not None
    assert await store.enqueue_recheck(source.hint) == "admitted"
    claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
    assert isinstance(claim, HistoryRecheckClaim)
    assert await store.record_recheck_statistics(claim, archived_statistics()) == "applied"
    before_statistics = await _archive_snapshot(engine)

    if order == "producer_first":
        async with admin.begin() as connection:
            await lock_workflow_observation(connection, observation.provenance.delivery_id)
            await connection.execute(
                delete(ci_history_delivery_inbox).where(
                    ci_history_delivery_inbox.c.delivery_id == observation.provenance.delivery_id
                )
            )
    async with engine.connect() as connection:
        eligible = (
            (
                await connection.execute(
                    select(ci_workflow_observations).where(
                        ci_workflow_observations.c.delivery_id
                        == observation.provenance.delivery_id,
                        ci_workflow_observations.c.retain_until <= func.statement_timestamp(),
                    )
                )
            )
            .mappings()
            .one()
        )
        assert dict(eligible) == dict(source_row)
    return observation, before_statistics


@pytest.mark.parametrize("order", ["producer_first", "cleanup_first"])
def test_source_inbox_cascade_race_uses_the_shared_guard_and_replays_safely(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    order: RaceOrder,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            observation, before_statistics = await _prepare_expired_source(engine, admin, order)
            delivery_id = observation.provenance.delivery_id
            expected_inbox = (delivery_id,) if order == "cleanup_first" else ()
            assert await _pair_snapshot(engine, delivery_id) == (
                (delivery_id,),
                expected_inbox,
            )

            native_cleanup_guard = try_lock_workflow_observations
            native_producer_guard = try_lock_workflow_observations
            cleanup_guard_acquired = asyncio.Event()
            cleanup_release = asyncio.Event()
            producer_child_inserted = asyncio.Event()
            producer_release = asyncio.Event()
            cleanup_pids: list[int] = []
            producer_pids: list[int] = []

            async def observe_cleanup_guard(
                connection: AsyncConnection, delivery_ids: tuple[str, ...]
            ) -> tuple[str, ...]:
                pid = await connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                cleanup_pids.append(pid)
                guarded = await native_cleanup_guard(connection, delivery_ids)
                if guarded and order == "cleanup_first" and not cleanup_guard_acquired.is_set():
                    cleanup_guard_acquired.set()
                    await cleanup_release.wait()
                return guarded

            async def observe_producer_guard(
                connection: AsyncConnection, delivery_ids: tuple[str, ...]
            ) -> tuple[str, ...]:
                pid = await connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                producer_pids.append(pid)
                return await native_producer_guard(connection, delivery_ids)

            # The wrapper calls the native restricted producer after the source guard is held.
            native_ensure = ensure_history_delivery_inbox

            async def gate_after_child_insert(
                connection: AsyncConnection, source_row: Mapping[str, object]
            ) -> bool:
                admitted = await native_ensure(connection, source_row)
                assert admitted is True
                pid = await connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                producer_pids.append(pid)
                producer_child_inserted.set()
                await producer_release.wait()
                return admitted

            if order == "producer_first":
                # The child INSERT is committed only after cleanup has observed the held guard.
                monkeypatch.setattr(
                    delivery_storage,
                    "ensure_history_delivery_inbox",
                    gate_after_child_insert,
                )
                monkeypatch.setattr(
                    economics_storage,
                    "try_lock_workflow_observations",
                    observe_cleanup_guard,
                )
                async with asyncio.timeout(15):
                    producer = asyncio.create_task(_runtime_ingest(engine, observation))
                    try:
                        await producer_child_inserted.wait()
                        assert await _cleanup_expired_source(engine) == 0
                        assert not producer.done()
                        assert await _pair_snapshot(engine, delivery_id) == (
                            (delivery_id,),
                            (),
                        )
                    finally:
                        producer_release.set()
                        if not producer.done():
                            await producer
                    assert isinstance(await producer, DeliveryDuplicate)
                assert len(cleanup_pids) == 1
                assert len(producer_pids) == 1
                assert cleanup_pids[0] != producer_pids[0]
                assert await _pair_snapshot(engine, delivery_id) == (
                    (delivery_id,),
                    (delivery_id,),
                )
                assert await _cleanup_expired_source(engine) == 1
                assert await _pair_snapshot(engine, delivery_id) == ((), ())
            else:
                # Cleanup owns the guard first; the producer rolls back and replays after CASCADE.
                monkeypatch.setattr(
                    economics_storage,
                    "try_lock_workflow_observations",
                    observe_cleanup_guard,
                )
                monkeypatch.setattr(
                    delivery_storage,
                    "try_lock_workflow_observations",
                    observe_producer_guard,
                )
                async with asyncio.timeout(15):
                    cleanup = asyncio.create_task(_cleanup_expired_source(engine))
                    try:
                        await cleanup_guard_acquired.wait()
                        assert isinstance(
                            await _runtime_ingest(engine, observation), DeliveryStoreUnavailable
                        )
                        assert await _pair_snapshot(engine, delivery_id) == (
                            (delivery_id,),
                            (delivery_id,),
                        )
                    finally:
                        cleanup_release.set()
                        if not cleanup.done():
                            await cleanup
                    assert await cleanup == 1
                assert len(cleanup_pids) == 1
                assert len(producer_pids) == 1
                assert cleanup_pids[0] != producer_pids[0]
                assert await _pair_snapshot(engine, delivery_id) == ((), ())
                assert isinstance(await _runtime_ingest(engine, observation), DeliveryDuplicate)
                assert await _pair_snapshot(engine, delivery_id) == (
                    (delivery_id,),
                    (delivery_id,),
                )

            assert await _archive_snapshot(engine) == before_statistics
            source_ids, inbox_ids = await _pair_snapshot(engine, delivery_id)
            assert inbox_ids <= source_ids
        finally:
            if "cleanup_release" in locals():
                cleanup_release.set()
            if "producer_release" in locals():
                producer_release.set()
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
