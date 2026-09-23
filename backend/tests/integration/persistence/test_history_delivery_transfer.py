import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics
from sqlalchemy import DateTime, delete, event, func, insert, literal, select, update
from sqlalchemy.exc import SQLAlchemyError

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_RUNS,
    HistoryRecheckClaim,
    HistoryRecheckHint,
    initial_history_recheck,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.github_ingestion import DeliveryClaimed, DeliveryDuplicate
from ci_coordinator.persistence import ci_history_delivery_transfer as transfer
from ci_coordinator.persistence.ci_history_recheck_codec import encode_history_recheck
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_delivery_inbox,
    ci_history_rechecks,
)
from ci_coordinator.persistence.workflow_observation_lock import lock_workflow_observation

from ._ci_economics_support import database_now
from ._history_support import history_command, history_store
from ._workflow_observation_support import ingest_workflow_observation, workflow_observation

pytestmark = pytest.mark.persistence


def test_capacity_commits_only_transferred_prefix_and_keeps_other_delivery_pending(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            configured = await store.configure_history(command)
            assert isinstance(configured, HistoryConfigured)
            now = await database_now(engine)
            rows = []
            for offset in range(MAX_HISTORY_RECHECK_RUNS - 1):
                hint = HistoryRecheckHint(
                    HistoryAttemptCursor(command.scope, 10000 + offset, 1),
                    10,
                    ARCHIVE_TIME,
                    "recent",
                )
                state = initial_history_recheck(configured.snapshot, hint, now)
                assert state is not None
                rows.append(encode_history_recheck(state))
            async with engine.begin() as connection:
                await connection.execute(insert(ci_history_rechecks), rows)
            for index, identity in enumerate(("a", "b")):
                observation = replace(
                    workflow_observation(identity, now), workflow_run_id=7001 + index
                )
                assert isinstance(
                    await ingest_workflow_observation(engine, observation), DeliveryClaimed
                )
            result = await store.transfer_deliveries(command.scope)
            assert (result.status, result.candidate_count, result.transferred_count) == (
                "capacity_reached",
                2,
                1,
            )
            async with engine.begin() as connection:
                receipts = tuple(
                    tuple(row)
                    for row in await connection.execute(
                        select(
                            ci_history_delivery_inbox.c.delivery_id,
                            ci_history_delivery_inbox.c.delivered_generation,
                        ).order_by(ci_history_delivery_inbox.c.delivery_id)
                    )
                )
                assert receipts == (("a", 1), ("b", 0))
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == MAX_HISTORY_RECHECK_RUNS
                )
                await connection.execute(
                    delete(ci_history_rechecks).where(
                        ci_history_rechecks.c.workflow_run_id == 10000
                    )
                )
            resumed = await store.transfer_deliveries(command.scope)
            assert (resumed.status, resumed.candidate_count, resumed.transferred_count) == (
                "applied",
                1,
                1,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_successful_delivery_receipt_survives_completed_queue_and_source_replay(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            observation = replace(
                workflow_observation("historical-delivery", ARCHIVE_TIME + timedelta(minutes=5)),
                workflow_run_id=303,
                workflow_id=404,
                head_sha="a" * 40,
                created_at=ARCHIVE_TIME,
            )
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            first = await store.transfer_deliveries(command.scope)
            assert (first.status, first.candidate_count, first.transferred_count) == (
                "applied",
                1,
                1,
            )
            async with engine.connect() as connection:
                receipt = (
                    (await connection.execute(select(ci_history_delivery_inbox))).mappings().one()
                )
                assert receipt["delivered_generation"] == 1 and receipt["delivered_at"] is not None
            claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(claim, HistoryRecheckClaim)
            assert await store.record_recheck_statistics(claim, archived_statistics()) == "applied"
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryDuplicate
            )
            second = await store.transfer_deliveries(command.scope)
            assert (second.status, second.candidate_count, second.transferred_count) == (
                "empty",
                0,
                0,
            )
            async with engine.connect() as connection:
                assert (
                    await connection.execute(select(ci_history_delivery_inbox))
                ).mappings().one() == receipt
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_attempts))
                    == 1
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cut", ["queue_insert", "receipt_update", "expiry"])
def test_transfer_rolls_back_queue_and_receipt_at_each_crash_cut(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    cut: Literal["queue_insert", "receipt_update", "expiry"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        rejected = False

        def reject(_connection: object, _cursor: object, statement: str, *_args: object) -> None:
            nonlocal rejected
            prefix = (
                "INSERT INTO ci_coordinator.ci_history_rechecks "
                if cut == "queue_insert"
                else "UPDATE ci_coordinator.ci_history_delivery_inbox "
            )
            if prefix in statement:
                rejected = True
                raise SQLAlchemyError("injected history transfer crash cut")

        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            observation = workflow_observation("pending-delivery", await database_now(engine))
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            async with engine.connect() as connection:
                before = (
                    (await connection.execute(select(ci_history_delivery_inbox))).mappings().one()
                )
            if cut == "expiry":
                with monkeypatch.context() as patch:
                    # noinspection PyUnresolvedReferences
                    patch.setattr(
                        transfer,
                        "history_cas_time",
                        lambda: literal(before["source_retain_until"], DateTime(timezone=True)),
                    )
                    result = await store.transfer_deliveries(command.scope)
                assert result.status == "deferred" and result.transferred_count == 0
            else:
                event.listen(engine.sync_engine, "after_cursor_execute", reject)
                try:
                    with pytest.raises(CiEconomicsStoreUnavailable):
                        await store.transfer_deliveries(command.scope)
                finally:
                    event.remove(engine.sync_engine, "after_cursor_execute", reject)
                assert rejected
            async with engine.connect() as connection:
                assert (
                    await connection.execute(select(ci_history_delivery_inbox))
                ).mappings().one() == before
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
            assert (await store.transfer_deliveries(command.scope)).transferred_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["missing", "paused", "unselected"])
def test_ineligible_sources_stay_pending_until_current_configuration_admits_them(
    runtime_postgres_database_url: str, state: Literal["missing", "paused", "unselected"]
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            observation = workflow_observation("unselected-delivery", await database_now(engine))
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            if state != "missing":
                original = ConfigureHistory.model_validate(
                    {
                        **command.model_dump(),
                        "configuration": {
                            **command.configuration.model_dump(),
                            "enabled": state != "paused",
                            "workflowIds": [11] if state == "unselected" else None,
                        },
                    }
                )
                assert isinstance(await store.configure_history(original), HistoryConfigured)
            result = await store.transfer_deliveries(command.scope)
            assert result.status == ("empty" if state == "unselected" else "inactive")
            assert result.transferred_count == 0
            enabled = ConfigureHistory.model_validate(
                {
                    **command.model_dump(),
                    "expectedRevision": 0 if state == "missing" else 1,
                    "operationId": "enable-pending-delivery",
                    "initialCreatedFrom": command.initial_created_from
                    if state == "missing"
                    else None,
                }
            )
            assert isinstance(await store.configure_history(enabled), HistoryConfigured)
            assert (await store.transfer_deliveries(command.scope)).transferred_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_busy_delivery_is_skipped_and_transfer_respects_its_bound(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            now = await database_now(engine)
            for index, identity in enumerate(("a", "b", "c")):
                observation = replace(
                    workflow_observation(identity, now), workflow_run_id=7001 + index
                )
                assert isinstance(
                    await ingest_workflow_observation(engine, observation), DeliveryClaimed
                )
            async with engine.connect() as holder, asyncio.timeout(10):
                await lock_workflow_observation(holder, "a")
                async with engine.begin() as connection:
                    result = await transfer.transfer_history_deliveries(
                        connection, command.scope, limit=2
                    )
                    assert (result.status, result.candidate_count, result.transferred_count) == (
                        "applied",
                        2,
                        1,
                    )
                await holder.rollback()
            assert (await store.transfer_deliveries(command.scope)).transferred_count == 2
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 3
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field",
    ["source_fingerprint", "workflow_run_id", "run_attempt", "workflow_id", "run_created_at"],
)
def test_pending_receipt_revalidates_every_independent_hint_operand(
    runtime_postgres_database_url: str, postgres_database_url: str, field: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            observation = workflow_observation("forged-delivery", await database_now(engine))
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            async with admin.begin() as connection:
                prior = (
                    (await connection.execute(select(ci_history_delivery_inbox))).mappings().one()
                )
                value = (
                    "0" * 64
                    if field == "source_fingerprint"
                    else prior[field] - timedelta(seconds=1)
                    if field == "run_created_at"
                    else prior[field] + 1
                )
                await connection.execute(update(ci_history_delivery_inbox).values({field: value}))
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.transfer_deliveries(command.scope)
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
                assert (
                    await connection.scalar(
                        select(ci_history_delivery_inbox.c.delivered_generation)
                    )
                    == 0
                )
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
