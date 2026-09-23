import asyncio
from dataclasses import replace
from typing import cast

import pytest
from ci_economics.gap_recovery_factories import gap_command, recheck_gap
from sqlalchemy import delete, func, select

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration, HistoryUsage
from ci_coordinator.ci_economics.history_gap_recovery import (
    HISTORY_GAPS_REQUEUED_EVENT_TYPE,
    HistoryGapRepairInterval,
    HistoryGapRepairReceipt,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
    write_history_dataset,
)
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_history_gaps, ci_history_rechecks, ci_history_scans

from ._history_support import history_store
from .test_history_gap_recovery import _seed

pytestmark = pytest.mark.persistence


def test_historical_replay_after_erased_postcondition_never_recreates_sources_or_work(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        try:
            command = gap_command(*(await _seed(store, 303)))
            first = await store.repair_history_gaps(command)
            assert first.outcome == "committed"
            # Prepare the erased postcondition; this is not a full erasure-lifecycle witness.
            async with admin.begin() as connection:
                await lock_history_scope(connection, command.scope)
                dataset = await load_history_dataset(connection, command.scope, locked=True)
                assert dataset is not None
                for table in (ci_history_rechecks, ci_history_gaps, ci_history_scans):
                    await connection.execute(
                        delete(table).where(history_scope_predicate(table, command.scope))
                    )
                erased = replace(
                    dataset,
                    state="erased",
                    data_revision=dataset.data_revision + 1,
                    configuration_revision=dataset.configuration_revision + 1,
                    usage=HistoryUsage.empty(),
                    configuration=HistoryConfiguration.model_validate(
                        {**dataset.configuration.model_dump(), "enabled": False}
                    ),
                )
                await write_history_dataset(connection, dataset, erased)
            replay = await store.repair_history_gaps(command)
            assert replay.outcome == "replayed" and replay.receipt == first.receipt
            fresh = RepairHistoryGaps.model_validate(
                {**command.model_dump(), "operationId": "new-operation"}
            )
            assert (await store.repair_history_gaps(fresh)).outcome == "dataset_fenced"
            async with engine.connect() as connection:
                assert await load_history_dataset(connection, command.scope) == erased
                for table in (ci_history_rechecks, ci_history_gaps, ci_history_scans):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("variant", ["source", "interval", "request", "actor"])
def test_valid_audit_bytes_do_not_mask_false_receipt_relations(
    runtime_postgres_database_url: str,
    variant: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        try:
            command = gap_command(*(await _seed(store, 303)))
            sources = [recheck_gap().model_dump(mode="json")]
            receipt = HistoryGapRepairReceipt(
                request=command.request,
                intervals=(
                    HistoryGapRepairInterval(workflowRunId=303, fromAttempt=1, throughAttempt=1),
                ),
            )
            if variant == "source":
                sources[0]["workflowId"] = 405
            if variant == "interval":
                receipt = HistoryGapRepairReceipt(
                    request=command.request,
                    intervals=(
                        HistoryGapRepairInterval(
                            workflowRunId=303, fromAttempt=1, throughAttempt=2
                        ),
                    ),
                )
            if variant == "request":
                receipt = HistoryGapRepairReceipt(
                    request=type(command.request).model_validate(
                        {**command.request.model_dump(), "repositoryId": 999}
                    ),
                    intervals=receipt.intervals,
                )
            async with PostgresHistoryUnitOfWork(engine) as unit:
                now = await history_database_time(unit.history_connection)
                event = prepare_audit_event(
                    AuditEventInput(
                        idempotency_key=command.audit_key,
                        subject_type="policy-decision",
                        subject_id="ci-history:101:202",
                        event_type=HISTORY_GAPS_REQUEUED_EVENT_TYPE,
                        created_at=now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        actor="other" if variant == "actor" else command.actor,
                        installation_id=101,
                        repository_id=202,
                        payload=cast(
                            JsonValue,
                            {
                                "commandDigest": command.command_digest,
                                "receipt": receipt.model_dump(mode="json"),
                                "sources": sources,
                            },
                        ),
                    )
                )
                assert isinstance(
                    await unit.history_audit._append_pair_owned(event, command.scope),
                    AuditAppendAppended,
                )
                await unit.commit()
            with pytest.raises(CiEconomicsStoreUnavailable) as refused:
                await store.repair_history_gaps(command)
            assert isinstance(refused.value.__cause__, ValueError)
            assert await store.claim_recheck(worker_id="a" * 64, source="repair") is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
