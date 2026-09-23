import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from ci_economics.archive_factories import archived_statistics, history_dataset
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.app.ci_history_collection import CiHistoryCollectionService
from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.discovery import (
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.history_rechecks import (
    HistoryRecheckClaim,
    HistoryRecheckHint,
    initial_history_recheck,
)
from ci_coordinator.ci_economics.history_scan import (
    HistoryClaim,
    HistoryScanState,
    RecentHistoryProgress,
)
from ci_coordinator.ci_economics.observation_scan import ObservationLease
from ci_coordinator.ci_economics.sources import (
    MAX_PROVIDER_SOURCES_PER_REPOSITORY,
    ProviderRunCollectionSource,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.persistence import ci_history_completion
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.ci_economics_source_registration import register_provider_sources
from ci_coordinator.persistence.ci_history_control_codec import (
    encode_history_dataset,
    encode_history_scan,
)
from ci_coordinator.persistence.ci_history_recheck_codec import (
    decode_history_recheck,
    encode_history_recheck,
)
from ci_coordinator.persistence.ci_history_state_store import load_history_scan, write_history_scan
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_datasets,
    ci_history_jobs,
    ci_history_rechecks,
    ci_history_scans,
    ci_workflow_attempt_collections,
    ci_workflow_observations,
)
from ci_coordinator.runtime.history_collection_worker import HistoryCollectionWorker

from ._ci_economics_support import database_now, provider_source
from ._history_support import history_store
from ._temporal_lock_support import wait_for_database_deadline

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("existing_hint", [False, True])
def test_discovery_queue_effect_rolls_back_when_terminal_cas_loses_its_lease(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    existing_hint: bool,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            scope, created = await _seed_previous_cycle(admin)
            store, provider = history_store(runtime), _Provider(scope, created)
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
            if existing_hint:
                assert (
                    await store.enqueue_recheck(
                        HistoryRecheckHint(
                            HistoryAttemptCursor(scope, 303, 1), 404, created, "recent"
                        )
                    )
                    == "admitted"
                )
            page_work = await store.claim_history(worker_id="a" * 64, lane="discovery")
            assert page_work is not None
            page = await provider.discover_observation_page(scope, cursor.window, page_number=1)
            source = replace(
                page.page.sources[0], attempt=replace(page.page.sources[0].attempt, run_attempt=2)
            )
            page = ProviderObservationPage(
                replace(page.page, provider_total=1, sources=(source,)), (404,)
            )
            assert await store.record_history_page(page_work[1], page) == "applied"
            work = await store.claim_history(worker_id="a" * 64, lane="discovery")
            assert work is not None and work[1].state.lease is not None
            lease = work[1].state.lease
            shift = lease.expires_at - await database_now(admin) - timedelta(seconds=4)
            short = replace(
                lease, acquired_at=lease.acquired_at - shift, expires_at=lease.expires_at - shift
            )
            claim = HistoryClaim(replace(work[1].state, lease=short))
            async with admin.begin() as connection:
                await connection.execute(
                    update(ci_history_scans)
                    .where(ci_history_scans.c.lane == "discovery")
                    .values(**encode_history_scan(claim.state))
                )
                before_queue = tuple(await connection.execute(select(ci_history_rechecks)))
                before_backfill = await load_history_scan(connection, scope)
            real_write = write_history_scan
            intercepted = False

            async def after_queue(
                connection: AsyncConnection,
                prior: HistoryScanState,
                successor: HistoryScanState,
                *,
                live_lease: ObservationLease | None = None,
            ) -> None:
                nonlocal intercepted
                row = (await connection.execute(select(ci_history_rechecks))).mappings().one()
                assert decode_history_recheck(row).hint.cursor.latest_attempt == 2
                intercepted = True
                await wait_for_database_deadline(admin, short.expires_at)
                await real_write(connection, prior, successor, live_lease=live_lease)

            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(ci_history_completion, "write_history_scan", after_queue)
                assert await store.handoff_discovered_history_run(claim) == "claim_lost"
            assert intercepted
            async with runtime.connect() as connection:
                assert tuple(await connection.execute(select(ci_history_rechecks))) == before_queue
                assert await load_history_scan(connection, scope, lane="discovery") == claim.state
                assert await load_history_scan(connection, scope) == before_backfill
            recovered = await store.claim_history(worker_id="b" * 64, lane="discovery")
            assert recovered is not None
            assert await store.handoff_discovered_history_run(recovered[1]) == "applied"
            async with runtime.connect() as connection:
                row = (await connection.execute(select(ci_history_rechecks))).mappings().one()
                assert decode_history_recheck(row).hint.cursor.latest_attempt == 2
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


async def _seed_previous_cycle(engine: AsyncEngine) -> tuple[RepositoryScope, datetime]:
    floor = (await database_now(engine)).replace(microsecond=0) - timedelta(minutes=2)
    dataset = replace(history_dataset(), configured_at=floor)
    cursor = HistoryCursor.start(dataset.scope, floor, floor, cycle_started_at=floor)
    backfill = HistoryScanState(dataset.scope, 1, 1, 1, HistoryCheckpoint(cursor), floor)
    recent = replace(
        backfill,
        checkpoint=HistoryCheckpoint(cursor, complete=True),
        recent=RecentHistoryProgress(floor, floor),
    )
    async with engine.begin() as connection:
        await connection.execute(
            insert(ci_history_datasets).values(**encode_history_dataset(dataset))
        )
        await connection.execute(
            insert(ci_history_scans), [encode_history_scan(backfill), encode_history_scan(recent)]
        )
    return dataset.scope, floor + timedelta(seconds=1)


class _Provider:
    def __init__(self, scope: RepositoryScope, created: datetime) -> None:
        self.scope, self.created = scope, created
        self.reads: list[int] = []

    async def allows_repository(self, scope: RepositoryScope) -> bool:
        assert scope == self.scope
        return True

    async def discover_observation_page(
        self, scope: RepositoryScope, window: RunDiscoveryWindow, *, page_number: int
    ) -> ProviderObservationPage:
        assert scope == self.scope
        sources = tuple(
            ProviderRunCollectionSource(
                self.statistics(run_id).attempt.to_attempt(), self.created, "2026-03-10", "b" * 64
            )
            for run_id in (303, 304)
            if window.created_from <= self.created <= window.created_through
        )
        return ProviderObservationPage(
            ProviderRunDiscoveryPage(
                scope, window, page_number, len(sources), sources, "exhausted"
            ),
            (404,) * len(sources),
        )

    def statistics(self, run_id: int) -> ArchivedAttemptStatistics:
        original = archived_statistics()
        return type(original).model_validate(
            {
                **original.model_dump(),
                "attempt": {**original.attempt.model_dump(), "workflowRunId": run_id},
                "runCreatedAt": self.created,
            }
        )

    async def load_history_attempt(
        self, cursor: HistoryAttemptCursor, *, run_created_at: datetime
    ) -> ArchivedAttemptStatistics:
        assert run_created_at == self.created
        self.reads.append(cursor.workflow_run_id)
        return self.statistics(cursor.workflow_run_id)


@pytest.mark.parametrize("active_queue_full", [False, True])
def test_completed_backfill_recovers_new_runs_without_webhook_or_active_evidence(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    active_queue_full: bool,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            scope, created = await _seed_previous_cycle(admin)
            store = history_store(runtime)
            provider = _Provider(scope, created)
            if active_queue_full:
                policy = load_bundled_ci_economics_profile().collection_policy
                now = await database_now(admin)
                async with admin.begin() as connection:
                    for first in range(1000, 1000 + MAX_PROVIDER_SOURCES_PER_REPOSITORY, 1000):
                        sources = (
                            provider_source(run, created) for run in range(first, first + 1000)
                        )
                        await connection.execute(
                            insert(ci_workflow_attempt_collections),
                            [
                                encode_collection_record(
                                    initial_collection_state(
                                        source.source_id, source.run_created_at, now, policy
                                    ),
                                    source,
                                )
                                for source in sources
                            ],
                        )
                    assert await register_provider_sources(
                        connection, scope, (provider_source(303, created),), policy
                    ) == ("capacity_reached",)
            service = CiHistoryCollectionService(
                store=store,
                discovery=provider,
                attempts=provider,
                repository_access=provider,
                worker_id="a" * 64,
                metrics=RuntimeMetrics(),
            )
            await service.run(asyncio.Event())
            initial = await store.history_status(scope)
            assert initial.scan is not None and initial.scan.checkpoint.complete
            assert initial.dataset is not None and initial.dataset.usage.attempts == 0
            worker = HistoryCollectionWorker(
                service.collect_next,
                idle_seconds=0.05,
                drain_seconds=5,
                metrics=RuntimeMetrics(),
            )
            await worker.start()
            try:
                async with asyncio.timeout(60):
                    while True:
                        progress = await store.history_status(scope)
                        if (
                            progress.dataset is not None
                            and progress.dataset.usage.attempts == 2
                            and progress.pending_rechecks == 0
                            and progress.discovery is not None
                            and progress.discovery.recent is not None
                            and progress.discovery.recent.completed_through is not None
                            and progress.discovery.recent.completed_through >= created
                        ):
                            break
                        await asyncio.sleep(0.05)
            finally:
                assert await worker.stop()
            status = await store.history_status(scope)
            assert status.scan is not None and status.scan.checkpoint.complete
            assert status.discovery is not None and status.discovery.recent is not None
            assert status.discovery.recent.completed_through is not None
            assert status.discovery.recent.completed_through >= created
            assert status.pending_rechecks == 0 and status.dataset is not None
            assert status.dataset.usage.attempts == status.dataset.usage.jobs == 2
            assert sorted(provider.reads) == [303, 304]
            async with runtime.connect() as connection:
                assert await connection.scalar(
                    select(func.count()).select_from(ci_workflow_attempt_collections)
                ) == (MAX_PROVIDER_SOURCES_PER_REPOSITORY if active_queue_full else 0)
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_attempts))
                    == 2
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_jobs)) == 2
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_observations)
                    )
                    == 0
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_partial_page_handoff_survives_capacity_refusal_and_store_restart(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            scope, created = await _seed_previous_cycle(admin)
            store = history_store(runtime)
            provider = _Provider(scope, created)
            work = await store.claim_history(worker_id="a" * 64, lane="discovery")
            assert work is not None
            dataset, claim = work
            cursor = claim.state.checkpoint.cursor
            page = await provider.discover_observation_page(
                scope, cursor.window, page_number=cursor.page_number
            )
            assert await store.record_history_page(claim, page) == "applied"
            filler = initial_history_recheck(
                dataset,
                HistoryRecheckHint(HistoryAttemptCursor(scope, 1000, 1), 404, created, "recent"),
                await database_now(admin),
            )
            assert filler is not None
            async with admin.begin() as connection:
                await connection.execute(
                    insert(ci_history_rechecks),
                    [
                        encode_history_recheck(
                            replace(
                                filler,
                                hint=replace(
                                    filler.hint, cursor=HistoryAttemptCursor(scope, run_id, 1)
                                ),
                            )
                        )
                        for run_id in range(1000, 1255)
                    ],
                )
            first = await store.claim_history(worker_id="a" * 64, lane="discovery")
            assert first is not None
            assert await store.handoff_discovered_history_run(first[1]) == "applied"
            second = await store.claim_history(worker_id="b" * 64, lane="discovery")
            assert second is not None
            assert await store.handoff_discovered_history_run(second[1]) == "capacity_reached"
            async with admin.begin() as connection:
                prior = await load_history_scan(connection, scope, lane="discovery")
                assert prior is not None and prior.checkpoint.pending is not None
                assert prior.checkpoint.pending.run_index == 1
                assert prior.checkpoint.pending.attempt_cursor.workflow_run_id == 304
                await connection.execute(
                    delete(ci_history_rechecks).where(ci_history_rechecks.c.workflow_run_id == 1000)
                )
                await connection.execute(
                    update(ci_history_scans)
                    .where(ci_history_scans.c.lane == "discovery")
                    .values(
                        **encode_history_scan(
                            replace(prior, next_attempt_at=await database_now(admin))
                        )
                    )
                )
            restarted = history_store(runtime)
            final = await restarted.claim_history(worker_id="c" * 64, lane="discovery")
            assert final is not None
            assert await restarted.handoff_discovered_history_run(final[1]) == "applied"
            assert await store.handoff_discovered_history_run(first[1]) == "claim_lost"
            async with runtime.connect() as connection:
                ids = (
                    await connection.scalars(select(ci_history_rechecks.c.workflow_run_id))
                ).all()
                assert len(ids) == len(set(ids)) == 256 and 303 in ids and 304 in ids
                scan = await load_history_scan(connection, scope, lane="discovery")
                assert (
                    scan is not None
                    and scan.checkpoint.complete
                    and scan.checkpoint.pending is None
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_nonterminal_wait_is_durable_without_retry_exhaustion(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            scope, created = await _seed_previous_cycle(admin)
            store = history_store(runtime)
            assert (
                await store.enqueue_recheck(
                    HistoryRecheckHint(HistoryAttemptCursor(scope, 303, 1), 404, created, "recent")
                )
                == "admitted"
            )
            for _ in range(5):
                claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
                assert isinstance(claim, HistoryRecheckClaim)
                assert claim.state.acquisition_count == 1
                assert await store.wait_for_history_attempt(claim) == "applied"
                async with admin.begin() as connection:
                    row = (await connection.execute(select(ci_history_rechecks))).mappings().one()
                    state = decode_history_recheck(row)
                    assert (
                        state.acquisition_count == 0
                        and state.next_attempt == 1
                        and state.lease is None
                    )
                    await connection.execute(
                        update(ci_history_rechecks).values(
                            **encode_history_recheck(
                                replace(state, next_attempt_at=await database_now(admin))
                            )
                        )
                    )
            terminal = await store.claim_recheck(worker_id="b" * 64, source="recent")
            assert isinstance(terminal, HistoryRecheckClaim)
            assert (
                await store.record_recheck_statistics(
                    terminal, _Provider(scope, created).statistics(303)
                )
                == "applied"
            )
            status = await store.history_status(scope)
            assert (
                status.pending_rechecks == 0
                and status.dataset is not None
                and status.dataset.usage.gaps == 0
            )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
