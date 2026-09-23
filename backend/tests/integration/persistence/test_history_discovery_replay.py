import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.app.ci_history_collection import CiHistoryCollectionService
from ci_coordinator.ci_economics.discovery import ProviderObservationPage
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_RUNS,
    HistoryRecheckHint,
    acquire_history_recheck,
    finish_history_recheck,
    initial_history_recheck,
)
from ci_coordinator.ci_economics.history_scan import HistoryClaim
from ci_coordinator.ci_economics.model import MAX_JOBS_PER_ATTEMPT
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.persistence.ci_history_control_codec import encode_history_scan
from ci_coordinator.persistence.ci_history_recheck_codec import encode_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    load_history_scan,
)
from ci_coordinator.persistence.ci_history_statistics_store import store_history_statistics
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_datasets,
    ci_history_details,
    ci_history_gaps,
    ci_history_jobs,
    ci_history_rechecks,
    ci_history_scans,
)

from ._ci_economics_support import database_now
from ._history_support import history_store
from ._temporal_lock_support import wait_for_database_deadline
from .test_history_recent_recovery import _Provider, _seed_previous_cycle

pytestmark = pytest.mark.persistence


async def _prepare(
    runtime: AsyncEngine, admin: AsyncEngine, case: str
) -> tuple[HistoryClaim, _Provider]:
    scope, created = await _seed_previous_cycle(admin)
    provider, store = _Provider(scope, created), history_store(runtime)
    if case == "lease_expiry":
        async with admin.begin() as connection:
            recent = await load_history_scan(connection, scope, lane="discovery")
            assert recent is not None and recent.recent is not None
            floor = recent.recent.recovery_floor
            cursor = HistoryCursor.start(
                scope,
                floor,
                floor + timedelta(seconds=60),
                cycle_started_at=floor + timedelta(seconds=60),
            )
            await connection.execute(
                update(ci_history_scans)
                .where(ci_history_scans.c.lane == "discovery")
                .values(
                    **encode_history_scan(replace(recent, checkpoint=HistoryCheckpoint(cursor)))
                )
            )
    work = await store.claim_history(worker_id="a" * 64, lane="discovery")
    assert work is not None
    page = await provider.discover_observation_page(
        scope, work[1].state.checkpoint.cursor.window, page_number=1
    )
    source = page.page.sources[0]
    record = provider.statistics(303)
    if case == "later_attempt":
        source = replace(source, attempt=replace(source.attempt, run_attempt=2))
        record = record.model_copy(
            update={"attempt": record.attempt.model_copy(update={"run_attempt": 2})}
        )
    elif case == "head":
        record = record.model_copy(
            update={"attempt": record.attempt.model_copy(update={"head_sha": "c" * 40})}
        )
    elif case == "workflow":
        record = record.model_copy(update={"workflow_id": 405})
    elif case == "creation":
        record = record.model_copy(update={"run_created_at": created + timedelta(seconds=1)})
    elif case == "partial":
        record = record.model_copy(update={"population": "partial", "provider_job_total": 2})
    elif case == "zero_jobs":
        record = record.model_copy(update={"jobs": (), "provider_job_total": 0})
    elif case == "large_complete":
        record = record.model_copy(
            update={
                "jobs": tuple(
                    record.jobs[0].model_copy(update={"provider_job_id": index})
                    for index in range(1, MAX_JOBS_PER_ATTEMPT + 1)
                ),
                "provider_job_total": MAX_JOBS_PER_ATTEMPT,
            }
        )
    elif case == "inconsistent_timing":
        record = record.model_copy(
            update={
                "jobs": (
                    record.jobs[0].model_copy(
                        update={"started_at": created - timedelta(seconds=1)}
                    ),
                )
            }
        )
    observed = ProviderObservationPage(
        replace(page.page, sources=(source,), provider_total=1), (404,)
    )
    assert await store.record_history_page(work[1], observed) == "applied"
    async with PostgresHistoryUnitOfWork(runtime) as transaction:
        connection = transaction.history_connection
        dataset = await load_history_dataset(connection, scope)
        assert dataset is not None
        now = await history_database_time(connection)
        dataset, outcome = await store_history_statistics(connection, dataset, record, now=now)
        assert outcome == "recorded"
        await transaction.commit()
    async with admin.begin() as connection:
        if case == "conflict":
            await connection.execute(update(ci_history_attempts).values(has_conflict=True))
        elif case == "missing_job":
            await connection.execute(delete(ci_history_jobs))
        elif case == "corrupt_digest":
            await connection.execute(update(ci_history_attempts).values(statistics_digest="f" * 64))
        elif case in {
            "queued_recent",
            "queued_repair",
            "queued_delayed",
            "queued_leased",
            "full_queue",
        }:
            hints = (
                HistoryRecheckHint(
                    HistoryAttemptCursor(scope, run, 1),
                    404,
                    created,
                    "repair" if case == "queued_repair" else "recent",
                )
                for run in (
                    range(1000, 1000 + MAX_HISTORY_RECHECK_RUNS) if case == "full_queue" else (303,)
                )
            )
            states = tuple(initial_history_recheck(dataset, hint, now) for hint in hints)
            assert all(state is not None for state in states)
            if case in {"queued_delayed", "queued_leased"}:
                state = states[0]
                assert state is not None
                acquired = acquire_history_recheck(
                    dataset, state, now=now, worker_id="c" * 64, token="d" * 64
                )
                assert acquired is not None
                if case == "queued_delayed":
                    transition = finish_history_recheck(
                        dataset, acquired.state, acquired, now=now, outcome="deferred"
                    )
                    assert transition is not None and transition.successor is not None
                    states = (transition.successor,)
                else:
                    states = (acquired.state,)
            await connection.execute(
                insert(ci_history_rechecks),
                [encode_history_recheck(state) for state in states if state is not None],
            )
    claim = await store.claim_history(worker_id="a" * 64, lane="discovery")
    assert claim is not None
    return claim[1], provider


async def _statistics_rows(engine: AsyncEngine) -> tuple[tuple[object, ...], ...]:
    async with engine.connect() as connection:
        rows = [
            tuple(await connection.execute(select(table).order_by(*table.primary_key.columns)))
            for table in (
                ci_history_datasets,
                ci_history_attempts,
                ci_history_jobs,
                ci_history_details,
                ci_history_gaps,
            )
        ]
        return tuple(rows)


async def _queue_rows(engine: AsyncEngine) -> tuple[object, ...]:
    async with engine.connect() as connection:
        return tuple(
            await connection.execute(
                select(ci_history_rechecks).order_by(*ci_history_rechecks.primary_key.columns)
            )
        )


@pytest.mark.parametrize(
    "case", ["complete", "zero_jobs", "large_complete", "inconsistent_timing", "full_queue"]
)
def test_exact_singleton_replay_skips_attempt_io_and_preserves_all_statistics(
    runtime_postgres_database_url: str, postgres_database_url: str, case: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            claim, provider = await _prepare(runtime, admin, case)
            store = history_store(runtime)
            before = await _statistics_rows(runtime)
            before_queue = await _queue_rows(runtime)
            assert await store.handoff_discovered_history_run(claim) == "applied"
            assert await store.handoff_discovered_history_run(claim) == "claim_lost"
            status = await store.history_status(provider.scope)
            assert status.discovery is not None
            assert status.discovery.last_outcome == "replayed"
            assert status.discovery.attempts_seen == 0
            assert status.discovery.checkpoint.complete
            assert await _statistics_rows(runtime) == before
            assert await _queue_rows(runtime) == before_queue
            async with runtime.connect() as connection:
                assert (
                    await connection.scalar(
                        select(ci_history_rechecks.c.workflow_run_id).where(
                            ci_history_rechecks.c.workflow_run_id == 303
                        )
                    )
                    is None
                )
            if case != "full_queue":
                service = CiHistoryCollectionService(
                    store=store,
                    discovery=provider,
                    attempts=provider,
                    repository_access=provider,
                    worker_id="b" * 64,
                    metrics=RuntimeMetrics(),
                )
                await service.run(asyncio.Event())
                assert provider.reads == []
            repair = HistoryRecheckHint(
                HistoryAttemptCursor(provider.scope, 303, 1), 404, provider.created, "repair"
            )
            if case != "full_queue":
                assert await store.enqueue_recheck(repair) == "admitted"
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "head",
        "workflow",
        "creation",
        "partial",
        "conflict",
        "missing_job",
        "corrupt_digest",
        "queued_recent",
        "queued_repair",
        "queued_delayed",
        "queued_leased",
        "later_attempt",
    ],
)
def test_ineligible_or_corrupt_fact_preserves_the_provider_recheck_path(
    runtime_postgres_database_url: str, postgres_database_url: str, case: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            claim, provider = await _prepare(runtime, admin, case)
            store = history_store(runtime)
            before = await _statistics_rows(runtime)
            before_queue = await _queue_rows(runtime)
            assert await store.handoff_discovered_history_run(claim) == "applied"
            status = await store.history_status(provider.scope)
            assert (
                status.discovery is not None and status.discovery.last_outcome == "recheck_queued"
            )
            assert status.pending_rechecks == 1
            assert await _statistics_rows(runtime) == before
            work = await store.claim_recheck(worker_id="b" * 64, source="recent")
            if case in {"queued_delayed", "queued_leased"}:
                assert work is None
                assert await _queue_rows(runtime) == before_queue
            else:
                assert not isinstance(work, str) and work is not None
                assert work.state.cursor.next_attempt == 1
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_replay_cannot_advance_after_lease_expiry(
    runtime_postgres_database_url: str, postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ci_coordinator.persistence import ci_history_completion

    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            claim, provider = await _prepare(runtime, admin, "lease_expiry")
            lease = claim.state.lease
            assert lease is not None
            shift = lease.expires_at - await database_now(admin) - timedelta(seconds=4)
            short = replace(
                lease, acquired_at=lease.acquired_at - shift, expires_at=lease.expires_at - shift
            )
            assert claim.state.checkpoint.cursor.cycle_started_at <= short.acquired_at
            claim = HistoryClaim(replace(claim.state, lease=short))
            async with admin.begin() as connection:
                await connection.execute(
                    update(ci_history_scans)
                    .where(ci_history_scans.c.lane == "discovery")
                    .values(**encode_history_scan(claim.state))
                )
            original = ci_history_completion._can_replay_discovered_fact

            async def expired(
                connection: AsyncConnection,
                dataset: HistoryDataset,
                source: ProviderRunCollectionSource,
                workflow_id: int,
            ) -> bool:
                result = await original(connection, dataset, source, workflow_id)
                assert result is True
                await wait_for_database_deadline(admin, short.expires_at)
                return result

            monkeypatch.setattr(ci_history_completion, "_can_replay_discovered_fact", expired)
            before = await _statistics_rows(runtime)
            assert (
                await history_store(runtime).handoff_discovered_history_run(claim) == "claim_lost"
            )
            assert await _statistics_rows(runtime) == before
            async with runtime.connect() as connection:
                assert (
                    await load_history_scan(connection, provider.scope, lane="discovery")
                    == claim.state
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
