import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME
from sqlalchemy import func, insert, select

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_REPAIR_RUNS,
    HistoryRecheckClaim,
    HistoryRecheckHint,
    initial_history_recheck,
)
from ci_coordinator.persistence.ci_history_recheck_codec import encode_history_recheck
from ci_coordinator.persistence.ci_history_recheck_rows import load_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    load_history_scan,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_history_gaps, ci_history_rechecks

from ._history_support import history_command, history_page, history_store

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("capacity", ["available", "gap", "queue"])
def test_handoff_advances_only_with_durable_gap_and_exact_queued_work(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    capacity: Literal["available", "gap", "queue"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        original = history_command()
        command = (
            original
            if capacity != "gap"
            else ConfigureHistory.model_validate(
                {
                    **original.model_dump(),
                    "configuration": {
                        **original.configuration.model_dump(),
                        "quota": {**original.configuration.quota.model_dump(), "canonicalBytes": 1},
                    },
                }
            )
        )
        try:
            configured = await store.configure_history(command)
            assert isinstance(configured, HistoryConfigured)
            queued = MAX_HISTORY_REPAIR_RUNS if capacity == "queue" else 0
            if queued:
                async with admin.begin() as connection:
                    now = await history_database_time(connection)
                    rows = []
                    for index in range(queued):
                        hint = HistoryRecheckHint(
                            HistoryAttemptCursor(command.scope, 1000 + index, 1),
                            404,
                            ARCHIVE_TIME,
                            "repair",
                        )
                        state = initial_history_recheck(configured.snapshot, hint, now)
                        assert state is not None
                        rows.append(encode_history_recheck(state))
                    await connection.execute(insert(ci_history_rechecks), rows)
            page = await store.claim_history(worker_id="a" * 64)
            assert page is not None
            assert await store.record_history_page(page[1], history_page(page[1])) == "applied"
            current = await store.claim_history(worker_id="a" * 64)
            assert current is not None
            before, claim = current
            assert await store.defer_history(claim, "provider_unavailable") == (
                "applied" if capacity == "available" else "capacity_reached"
            )
            async with engine.connect() as connection:
                scan = await load_history_scan(connection, command.scope)
                after = await load_history_dataset(connection, command.scope)
                assert scan is not None and after is not None and scan.lease is None
                count = await connection.scalar(select(func.count()).select_from(ci_history_gaps))
                queue_count = await connection.scalar(
                    select(func.count()).select_from(ci_history_rechecks)
                )
                if capacity == "available":
                    assert count == queue_count == 1
                    state = await load_history_recheck(connection, command.scope, 1, 303)
                    assert state is not None
                    assert state.cursor == HistoryAttemptCursor(command.scope, 303, 1)
                    assert state.hint.source == "repair" and state.hint.workflow_id == 404
                    assert after.usage.gaps == 1 and scan.last_outcome == "recheck_queued"
                    assert scan.checkpoint != claim.state.checkpoint
                else:
                    assert (count, queue_count) == (0, queued)
                    assert after == before and scan.checkpoint == claim.state.checkpoint
                    assert scan.last_outcome == "capacity_reached"
                assert scan.revision == claim.state.revision + 1
            assert await store.defer_history(claim, "timed_out") == "claim_lost"
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_parent_claim_repair_rewinds_completed_attempt_without_replay_authority(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            hint = HistoryRecheckHint(
                HistoryAttemptCursor(command.scope, 303, 4), 404, ARCHIVE_TIME, "recent"
            )
            assert await store.enqueue_recheck(hint) == "admitted"
            for attempt in (1, 2):
                claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
                assert (
                    isinstance(claim, HistoryRecheckClaim) and claim.state.next_attempt == attempt
                )
                assert await store.record_recheck_failure(claim, missing=True) == "applied"
            page_claim = await store.claim_history(worker_id="a" * 64)
            assert page_claim is not None
            observed = history_page(page_claim[1])
            source = observed.page.sources[0]
            observed = replace(
                observed,
                page=replace(
                    observed.page,
                    sources=(replace(source, attempt=replace(source.attempt, run_attempt=4)),),
                ),
            )
            assert await store.record_history_page(page_claim[1], observed) == "applied"
            first = await store.claim_history(worker_id="a" * 64)
            assert first is not None
            assert await store.record_unavailable_history_attempt(first[1]) == "applied"
            second = await store.claim_history(worker_id="a" * 64)
            assert second is not None and second[1].state.checkpoint.pending is not None
            assert second[1].state.checkpoint.pending.attempt_cursor.next_attempt == 2
            assert await store.defer_history(second[1], "provider_malformed") == "applied"
            repaired = await store.claim_recheck(worker_id="b" * 64, source="recent")
            assert isinstance(repaired, HistoryRecheckClaim)
            assert repaired.state.next_attempt == 2 and repaired.state.acquisition_count == 1
            assert await store.record_recheck_failure(repaired, missing=True) == "applied"
            assert await store.defer_history(second[1], "provider_malformed") == "claim_lost"
            assert await store.enqueue_recheck(hint) == "replayed"
            async with engine.connect() as connection:
                current = await load_history_recheck(connection, command.scope, 1, 303)
                assert current is not None and current.next_attempt == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())
