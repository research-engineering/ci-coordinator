import asyncio
from typing import Literal

import pytest
from app._history_collection_support import CollectionBoundary
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics, history_scan
from ci_economics.gap_recovery_factories import gap_command, recheck_gap
from sqlalchemy import func, insert, select

from ci_coordinator.app.ci_history_collection import CiHistoryCollectionService
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_gap import HistoryGap, HistoryRecheckGap
from ci_coordinator.ci_economics.history_gap_recovery import (
    HISTORY_GAPS_REQUEUED_EVENT_TYPE,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_REPAIR_RUNS,
    HistoryRecheckClaim,
    HistoryRecheckHint,
    initial_history_recheck,
)
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.persistence.ci_history_adapters import TransactionalHistoryStore
from ci_coordinator.persistence.ci_history_gap_recovery import repair_history_gaps
from ci_coordinator.persistence.ci_history_gap_store import store_history_gap
from ci_coordinator.persistence.ci_history_recheck_codec import encode_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    lock_history_scope,
)
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_history_attempts,
    ci_history_gaps,
    ci_history_rechecks,
)

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence


async def _seed(store: TransactionalHistoryStore, *runs: int) -> tuple[HistoryRecheckGap, ...]:
    configured = await store.configure_history(history_command())
    assert isinstance(configured, HistoryConfigured)
    gaps = []
    for run in runs:
        hint = HistoryRecheckHint(
            HistoryAttemptCursor(configured.snapshot.scope, run, 1), 404, ARCHIVE_TIME, "repair"
        )
        assert await store.enqueue_recheck(hint) == "admitted"
        claim = await store.claim_recheck(worker_id="a" * 64, source="repair")
        assert isinstance(claim, HistoryRecheckClaim) and claim.state.cursor.workflow_run_id == run
        assert await store.record_recheck_failure(claim, missing=True) == "applied"
        gaps.append(recheck_gap(run))
    return tuple(sorted(gaps, key=lambda gap: gap.gap_id))


def test_page_gap_without_workflow_metadata_cannot_authorize_a_targeted_retry(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        try:
            await _seed(store)
            command = gap_command()
            gap = HistoryGap(
                schemaVersion="ci-economics-history-gap/v1",
                generation=1,
                configurationRevision=1,
                reason="provider_truncated",
                workflowRunId=None,
                runAttempt=None,
                cursor=HistoryCursorPayload.model_validate(
                    history_scan().checkpoint.cursor.canonical_mapping()
                ),
            )
            async with PostgresHistoryUnitOfWork(engine) as unit:
                await lock_history_scope(unit.history_connection, command.scope)
                dataset = await load_history_dataset(
                    unit.history_connection, command.scope, locked=True
                )
                assert dataset is not None
                _, outcome = await store_history_gap(
                    unit.history_connection,
                    dataset,
                    gap,
                    now=await history_database_time(unit.history_connection),
                )
                assert outcome == "recorded"
                await unit.commit()
            command = RepairHistoryGaps.model_validate(
                {**command.model_dump(), "gapIds": (gap.gap_id,)}
            )
            result = await store.repair_history_gaps(command)
            assert result.outcome == "unsupported_gap" and result.receipt is None
            assert await store.claim_recheck(worker_id="a" * 64, source="repair") is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("runs", [(303,), (303, 304)])
def test_operator_retry_collects_missing_attempt_and_replay_cannot_resurrect_work(
    runtime_postgres_database_url: str,
    runs: tuple[int, ...],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        try:
            gaps = await _seed(store, *runs)
            command = gap_command(*gaps)
            async with engine.connect() as connection:
                before = tuple(
                    (
                        await connection.execute(
                            select(ci_history_gaps).order_by(ci_history_gaps.c.gap_id)
                        )
                    ).all()
                )
            first, concurrent = await asyncio.gather(
                store.repair_history_gaps(command), store.repair_history_gaps(command)
            )
            assert {first.outcome, concurrent.outcome} == {"committed", "replayed"}
            assert first.receipt == concurrent.receipt
            boundary = CollectionBoundary(attempt=archived_statistics())
            service = CiHistoryCollectionService(
                store=store,
                discovery=boundary,
                attempts=boundary,
                repository_access=boundary,
                worker_id="a" * 64,
                metrics=RuntimeMetrics(),
            )
            for run in runs:
                original = archived_statistics()
                boundary.attempt = type(original).model_validate(
                    {
                        **original.model_dump(),
                        "attempt": {**original.attempt.model_dump(), "workflowRunId": run},
                    }
                )
                assert await service.collect_next("repair", asyncio.Event()) == "applied"
            replay = await store.repair_history_gaps(command)
            assert replay.outcome == "replayed" and replay.receipt == first.receipt
            assert await store.claim_recheck(worker_id="b" * 64, source="repair") is None
            changed = RepairHistoryGaps.model_validate({**command.model_dump(), "actor": "other"})
            assert (await store.repair_history_gaps(changed)).outcome == "operation_conflict"
            async with engine.connect() as connection:
                assert (
                    tuple(
                        (
                            await connection.execute(
                                select(ci_history_gaps).order_by(ci_history_gaps.c.gap_id)
                            )
                        ).all()
                    )
                    == before
                )
                assert await connection.scalar(
                    select(func.count()).select_from(ci_history_attempts)
                ) == len(runs)
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(audit_events)
                        .where(
                            audit_events.c.event_type == HISTORY_GAPS_REQUEUED_EVENT_TYPE.encode()
                        )
                    )
                    == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("vacancies", [0, 1])
def test_late_queue_capacity_refusal_rolls_back_the_entire_batch(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    vacancies: int,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        try:
            gaps = await _seed(store, 303, 304)
            command = gap_command(*gaps)
            async with admin.begin() as connection:
                dataset = await load_history_dataset(connection, command.scope)
                assert dataset is not None
                now = await history_database_time(connection)
                states = [
                    initial_history_recheck(
                        dataset,
                        HistoryRecheckHint(
                            HistoryAttemptCursor(command.scope, 1000 + index, 1),
                            404,
                            ARCHIVE_TIME,
                            "repair",
                        ),
                        now,
                    )
                    for index in range(MAX_HISTORY_REPAIR_RUNS - vacancies)
                ]
                assert all(state is not None for state in states)
                await connection.execute(
                    insert(ci_history_rechecks),
                    [encode_history_recheck(state) for state in states if state is not None],
                )
            async with PostgresHistoryUnitOfWork(engine) as unit:
                result = await repair_history_gaps(
                    unit.history_connection, unit.history_audit, command
                )
                await unit.commit()
            assert result.outcome == "capacity_reached" and result.receipt is None
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == MAX_HISTORY_REPAIR_RUNS - vacancies
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(ci_history_rechecks)
                        .where(ci_history_rechecks.c.workflow_run_id.in_((303, 304)))
                    )
                    == 0
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(audit_events)
                        .where(
                            audit_events.c.event_type == HISTORY_GAPS_REQUEUED_EVENT_TYPE.encode()
                        )
                    )
                    == 0
                )
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "enabled,workflows,expected",
    [
        (False, None, "dataset_fenced"),
        (True, (405,), "workflow_unselected"),
    ],
)
def test_current_policy_fences_new_command_but_preserves_historical_receipt(
    runtime_postgres_database_url: str,
    enabled: bool,
    workflows: tuple[int, ...] | None,
    expected: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        try:
            command = gap_command(*(await _seed(store, 303)))
            admitted = await store.repair_history_gaps(command)
            assert admitted.outcome == "committed"
            configured = history_command()
            change = ConfigureHistory.model_validate(
                {
                    **configured.model_dump(),
                    "expectedRevision": 1,
                    "initialCreatedFrom": None,
                    "operationId": "change-policy",
                    "configuration": {
                        **configured.configuration.model_dump(),
                        "enabled": enabled,
                        "workflowIds": workflows,
                    },
                }
            )
            assert isinstance(await store.configure_history(change), HistoryConfigured)
            replay = await store.repair_history_gaps(command)
            assert replay.outcome == "replayed" and replay.receipt == admitted.receipt
            new = RepairHistoryGaps.model_validate(
                {**command.model_dump(), "expectedRevision": 2, "operationId": "new-retry"}
            )
            assert (await store.repair_history_gaps(new)).outcome == expected
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("generation", "generation_conflict"),
        ("revision", "revision_conflict"),
        ("missing", "gap_not_found"),
        ("foreign", "dataset_fenced"),
    ],
)
def test_stale_or_foreign_selection_has_no_queue_or_audit_effect(
    runtime_postgres_database_url: str,
    variant: Literal["generation", "revision", "missing", "foreign"],
    expected: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        try:
            command = gap_command(*(await _seed(store, 303)))
            changes: dict[str, dict[str, object]] = {
                "generation": {"generation": 2},
                "revision": {"expectedRevision": 2},
                "missing": {"gapIds": ("f" * 64,)},
                "foreign": {"repositoryId": 999},
            }
            command = RepairHistoryGaps.model_validate({**command.model_dump(), **changes[variant]})
            assert (await store.repair_history_gaps(command)).outcome == expected
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(audit_events)
                        .where(
                            audit_events.c.event_type == HISTORY_GAPS_REQUEUED_EVENT_TYPE.encode()
                        )
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_operator_rewind_invalidates_older_worker_without_discarding_later_work(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        try:
            command = gap_command(*(await _seed(store, 303)))
            assert (
                await store.enqueue_recheck(
                    HistoryRecheckHint(
                        HistoryAttemptCursor(command.scope, 303, 3), 404, ARCHIVE_TIME, "recent"
                    )
                )
                == "admitted"
            )
            first = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(first, HistoryRecheckClaim)
            assert await store.record_recheck_failure(first, missing=True) == "applied"
            old = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(old, HistoryRecheckClaim) and old.state.next_attempt == 2
            assert (await store.repair_history_gaps(command)).outcome == "committed"
            assert await store.record_recheck_failure(old, missing=True) == "claim_lost"
            current = await store.claim_recheck(worker_id="b" * 64, source="recent")
            assert isinstance(current, HistoryRecheckClaim)
            assert current.state.next_attempt == 1 and current.state.hint.cursor.latest_attempt == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())
