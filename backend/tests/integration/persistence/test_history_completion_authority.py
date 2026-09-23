import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import archived_statistics, history_dataset, history_scan
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration.persistence._ci_economics_support import database_now
from tests.integration.persistence._temporal_lock_support import wait_for_database_deadline

from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_gap import HistoryGap
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.history_scan import HistoryClaim, HistoryScanState
from ci_coordinator.ci_economics.observation_scan import ObservationLease
from ci_coordinator.persistence import ci_history_completion
from ci_coordinator.persistence.ci_history_control_codec import (
    encode_history_dataset,
    encode_history_scan,
)
from ci_coordinator.persistence.ci_history_gap_store import store_history_gap
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    load_history_scan,
    lock_history_scope,
    write_history_scan,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_datasets,
    ci_history_gaps,
    ci_history_jobs,
    ci_history_rechecks,
    ci_history_scans,
)

from ._history_support import history_command, history_page, history_store

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("commit", [False, True])
def test_immutable_gap_replay_uses_parent_lock_without_update_privilege(
    runtime_postgres_database_url: str, commit: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            claimed = await store.claim_history(worker_id="a" * 64)
            assert claimed is not None
            original, claim = claimed
            gap = HistoryGap(
                schemaVersion="ci-economics-history-gap/v1",
                generation=original.generation,
                configurationRevision=original.configuration_revision,
                cursor=HistoryCursorPayload.model_validate(
                    claim.state.checkpoint.cursor.canonical_mapping()
                ),
                reason="provider_truncated",
                workflowRunId=None,
                runAttempt=None,
            )
            async with engine.connect() as holder:
                transaction = await holder.begin()
                try:
                    assert (
                        await holder.scalar(
                            text(
                                "SELECT has_table_privilege("
                                "'ci_coordinator.ci_history_gaps', 'UPDATE')"
                            )
                        )
                        is False
                    )
                    await lock_history_scope(holder, command.scope)
                    assert (
                        await load_history_dataset(holder, command.scope, locked=True) == original
                    )
                    changed, outcome = await store_history_gap(
                        holder, original, gap, now=await history_database_time(holder)
                    )
                    assert outcome == "recorded"
                    assert (
                        changed.usage.gaps == 1
                        and changed.data_revision == original.data_revision + 1
                    )
                    async with engine.begin() as competitor:
                        assert not await lock_history_scope(
                            competitor, command.scope, try_only=True
                        )
                        assert await load_history_dataset(competitor, command.scope) == original
                        assert (
                            await competitor.scalar(
                                select(func.count()).select_from(ci_history_gaps)
                            )
                            == 0
                        )
                    if commit:
                        await transaction.commit()
                    else:
                        await transaction.rollback()
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
            async with engine.begin() as successor:
                assert await lock_history_scope(successor, command.scope, try_only=True)
                prior = await load_history_dataset(successor, command.scope, locked=True)
                assert prior == (changed if commit else original)
                assert prior is not None
                final, outcome = await store_history_gap(
                    successor, prior, gap, now=await history_database_time(successor)
                )
                assert outcome == ("replayed" if commit else "recorded")
                assert final == changed
                assert (
                    await successor.scalar(select(func.count()).select_from(ci_history_gaps)) == 1
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("workflow_id", [404, 405])
@pytest.mark.parametrize("missing", [False, True])
def test_completion_requires_selected_workflow_and_only_skip_advances_excluded_work(
    runtime_postgres_database_url: str, workflow_id: int, missing: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        baseline = history_command()
        command = ConfigureHistory.model_validate(
            {
                **baseline.model_dump(),
                "configuration": {
                    **baseline.configuration.model_dump(),
                    "workflowIds": [workflow_id],
                },
            }
        )
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            page_claim = await store.claim_history(worker_id="a" * 64)
            assert page_claim is not None
            assert await store.record_history_page(page_claim[1], history_page(page_claim[1])) == (
                "applied"
            )
            claimed = await store.claim_history(worker_id="a" * 64)
            assert claimed is not None
            before, claim = claimed
            result = (
                await store.record_unavailable_history_attempt(claim)
                if missing
                else await store.record_history_statistics(claim, archived_statistics())
            )
            selected = workflow_id == 404
            assert result == ("applied" if selected else "claim_lost")
            async with engine.connect() as connection:
                after = await load_history_dataset(connection, command.scope)
                scan = await load_history_scan(connection, command.scope)
                assert after is not None and scan is not None
                expected_counts = ((0, 0, 1) if missing else (1, 1, 0)) if selected else (0, 0, 0)
                assert await _contribution_counts(connection) == expected_counts
                if not selected:
                    assert (after, scan) == (before, claim.state)
            if not selected:
                assert await store.skip_unselected_history_run(claim) == "applied"
                async with engine.connect() as connection:
                    scan = await load_history_scan(connection, command.scope)
                    assert scan is not None and scan.last_outcome == "unselected"
                    assert scan.checkpoint != claim.state.checkpoint
                    assert await _contribution_counts(connection) == (0, 0, 0)
                    assert await load_history_dataset(connection, command.scope) == before
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("repair", [False, True])
def test_sql_expiry_after_contribution_writes_rolls_back_and_only_fresh_reclaim_commits(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    repair: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        dataset = history_dataset()
        statistics = archived_statistics(population="partial", provider_total=2)
        try:
            async with admin.begin() as connection:
                await connection.execute(
                    insert(ci_history_datasets).values(**encode_history_dataset(dataset))
                )
                await connection.execute(
                    insert(ci_history_scans).values(
                        **encode_history_scan(history_scan(dataset, days=1))
                    )
                )
            page_claim = await store.claim_history(worker_id="a" * 64)
            assert page_claim is not None
            assert await store.record_history_page(page_claim[1], history_page(page_claim[1])) == (
                "applied"
            )
            claimed = await store.claim_history(worker_id="a" * 64)
            assert claimed is not None and claimed[1].state.lease is not None
            original_lease = claimed[1].state.lease
            shift = original_lease.expires_at - await database_now(admin) - timedelta(seconds=4)
            lease = replace(
                original_lease,
                acquired_at=original_lease.acquired_at - shift,
                expires_at=original_lease.expires_at - shift,
            )
            claim = HistoryClaim(replace(claimed[1].state, lease=lease))
            async with admin.begin() as connection:
                await connection.execute(
                    update(ci_history_scans).values(**encode_history_scan(claim.state))
                )
            real_write = write_history_scan
            intercepted = False

            async def delayed_terminal_cas(
                connection: AsyncConnection,
                prior: HistoryScanState,
                successor: HistoryScanState,
                *,
                live_lease: ObservationLease | None = None,
            ) -> None:
                nonlocal intercepted
                intercepted = True
                assert live_lease == lease and prior == claim.state
                assert await _contribution_counts(connection) == (
                    (0, 0, 1) if repair else (1, 1, 1)
                )
                assert await connection.scalar(
                    select(func.count()).select_from(ci_history_rechecks)
                ) == int(repair)
                changed = await load_history_dataset(connection, dataset.scope)
                assert changed is not None and changed.data_revision > dataset.data_revision
                assert changed.usage.attempts == changed.usage.jobs == int(not repair)
                assert changed.usage.gaps == 1
                assert changed.usage.canonical_bytes > 0
                await wait_for_database_deadline(admin, lease.expires_at)
                await real_write(connection, prior, successor, live_lease=live_lease)

            async with asyncio.timeout(12):
                with monkeypatch.context() as patch:
                    # noinspection PyUnresolvedReferences
                    patch.setattr(ci_history_completion, "write_history_scan", delayed_terminal_cas)
                    result = (
                        await store.defer_history(claim, "provider_unavailable")
                        if repair
                        else await store.record_history_statistics(claim, statistics)
                    )
                    assert result == "claim_lost"
            assert intercepted
            async with engine.connect() as connection:
                assert await _contribution_counts(connection) == (0, 0, 0)
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
                assert await load_history_dataset(connection, dataset.scope) == dataset
                assert await load_history_scan(connection, dataset.scope) == claim.state
            reclaimed = await store.claim_history(worker_id="b" * 64)
            assert reclaimed is not None and reclaimed[1].state.lease is not None
            assert reclaimed[1].state.lease.token != lease.token
            assert reclaimed[1].state.revision == claim.state.revision + 1
            assert await store.record_history_statistics(claim, statistics) == "claim_lost"
            result = (
                await store.defer_history(reclaimed[1], "provider_unavailable")
                if repair
                else await store.record_history_statistics(reclaimed[1], statistics)
            )
            assert result == "applied"
            async with engine.connect() as connection:
                assert await _contribution_counts(connection) == (
                    (0, 0, 1) if repair else (1, 1, 1)
                )
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


async def _contribution_counts(connection: AsyncConnection) -> tuple[int, ...]:
    result: list[int] = []
    for table in (ci_history_attempts, ci_history_jobs, ci_history_gaps):
        count = await connection.scalar(select(func.count()).select_from(table))
        assert type(count) is int
        result.append(count)
    return tuple(result)
