import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import (
    ARCHIVE_TIME,
    archived_detail,
    archived_statistics,
    history_dataset,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration.persistence._ci_economics_support import database_now
from tests.integration.persistence._temporal_lock_support import wait_for_database_deadline

from ci_coordinator.ci_economics.archive_detail import encode_archive_detail
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration
from ci_coordinator.ci_economics.history_rechecks import (
    HistoryRecheckClaim,
    HistoryRecheckHint,
    HistoryRecheckSource,
    HistoryRecheckState,
)
from ci_coordinator.persistence import ci_history_recheck_completion
from ci_coordinator.persistence.ci_history_codec import encode_archive_statistics
from ci_coordinator.persistence.ci_history_control_codec import encode_history_dataset
from ci_coordinator.persistence.ci_history_recheck_codec import encode_history_recheck
from ci_coordinator.persistence.ci_history_recheck_rows import (
    RecheckCasAuthority,
    load_history_recheck,
    write_history_recheck,
)
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_retention
from ci_coordinator.persistence.ci_history_state_store import load_history_dataset
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_attempts,
    ci_history_datasets,
    ci_history_details,
    ci_history_gaps,
    ci_history_jobs,
    ci_history_rechecks,
)

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence


def _hint(source: HistoryRecheckSource = "recent") -> HistoryRecheckHint:
    return HistoryRecheckHint(
        HistoryAttemptCursor(history_dataset().scope, 303, 1), 404, ARCHIVE_TIME, source
    )


@pytest.mark.parametrize("source", ["recent", "repair"])
@pytest.mark.parametrize("with_detail", [False, True])
def test_exact_replay_and_reimport_preserve_statistics_and_first_import(
    runtime_postgres_database_url: str,
    source: HistoryRecheckSource,
    with_detail: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        statistics = archived_statistics()
        detail = archived_detail() if with_detail else None
        hint = _hint(source)
        try:
            assert isinstance(await store.configure_history(history_command()), HistoryConfigured)
            assert await store.enqueue_recheck(hint) == "admitted"
            assert await store.enqueue_recheck(hint) == "replayed"
            claim = await store.claim_recheck(worker_id="a" * 64, source=source)
            assert isinstance(claim, HistoryRecheckClaim)
            assert await store.claim_recheck(worker_id="b" * 64, source=source) is None
            assert (
                await store.record_recheck_statistics(claim, statistics, detail=detail) == "applied"
            )
            assert (
                await store.record_recheck_statistics(claim, statistics, detail=detail)
                == "claim_lost"
            )
            async with engine.connect() as connection:
                first = (await connection.execute(select(ci_history_attempts))).mappings().one()
                before = await load_history_dataset(connection, hint.cursor.scope)
                assert before is not None
                children = (await connection.execute(select(ci_history_details))).mappings().all()
                assert len(children) == int(with_detail)
                encoded = b"" if detail is None else encode_archive_detail(detail)
                if detail is not None:
                    assert children[0]["detail_canonical"] == encoded
                    retention = decode_history_retention(first)
                    assert retention.state == "retained" and retention.first_imported_at is not None
                assert before.usage.canonical_bytes == (
                    encode_archive_statistics(statistics, generation=1).canonical_bytes
                    + len(encoded)
                )
                assert await _counts(connection) == (1, 1, 0, 0)
            assert await store.enqueue_recheck(hint) == "admitted"
            replay = await store.claim_recheck(worker_id="b" * 64, source=source)
            assert isinstance(replay, HistoryRecheckClaim)
            assert (
                await store.record_recheck_statistics(replay, statistics, detail=detail)
                == "applied"
            )
            async with engine.connect() as connection:
                assert (
                    await connection.execute(select(ci_history_attempts))
                ).mappings().one() == first
                assert await load_history_dataset(connection, hint.cursor.scope) == before
                assert (
                    await connection.execute(select(ci_history_details))
                ).mappings().all() == children
                assert await _counts(connection) == (1, 1, 0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("with_detail", [False, True])
def test_capacity_rejection_rolls_back_statistics_and_preserves_retry_work(
    runtime_postgres_database_url: str,
    with_detail: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        statistics = archived_statistics()
        detail = archived_detail() if with_detail else None
        quota = (
            encode_archive_statistics(statistics, generation=1).canonical_bytes
            + len(encode_archive_detail(detail))
            - 1
            if detail is not None
            else 1
        )
        configuration = HistoryConfiguration.model_validate(
            {
                **command.configuration.model_dump(),
                "quota": {**command.configuration.quota.model_dump(), "canonicalBytes": quota},
            }
        )
        try:
            assert isinstance(
                await store.configure_history(
                    ConfigureHistory.model_validate(
                        {
                            **command.model_dump(),
                            "configuration": configuration.model_dump(),
                        }
                    )
                ),
                HistoryConfigured,
            )
            assert await store.enqueue_recheck(_hint()) == "admitted"
            claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(claim, HistoryRecheckClaim)
            assert (
                await store.record_recheck_statistics(claim, statistics, detail=detail)
                == "capacity_reached"
            )
            async with engine.connect() as connection:
                state = await load_history_recheck(connection, _hint().cursor.scope, 1, 303)
                assert state is not None and state.hint == claim.state.hint
                assert (
                    state.next_attempt == claim.state.next_attempt and state.acquisition_count == 0
                )
                assert state.lease is None and state.next_attempt_at > claim.state.next_attempt_at
                assert await _counts(connection) == (0, 0, 0, 1)
                assert not (await connection.execute(select(ci_history_details))).all()
            assert (
                await store.record_recheck_statistics(claim, archived_statistics()) == "claim_lost"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_exhausted_gap_quota_does_not_starve_a_healthy_run_in_the_same_scope(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        original = history_command()
        configuration = {
            **original.configuration.model_dump(),
            "quota": {**original.configuration.quota.model_dump(), "gaps": 1},
        }
        try:
            command = ConfigureHistory.model_validate(
                {**original.model_dump(), "configuration": configuration}
            )
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            filler = replace(_hint(), cursor=replace(_hint().cursor, workflow_run_id=301))
            assert await store.enqueue_recheck(filler) == "admitted"
            first = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(first, HistoryRecheckClaim)
            assert await store.record_recheck_failure(first, missing=True) == "applied"
            blocked = replace(_hint(), cursor=replace(_hint().cursor, workflow_run_id=302))
            assert await store.enqueue_recheck(blocked) == "admitted"
            for count in (1, 2, 3):
                claim = await store.claim_recheck(worker_id="a" * 64, source="recent")
                assert isinstance(claim, HistoryRecheckClaim)
                assert claim.state.acquisition_count == count and claim.state.lease is not None
                lease = claim.state.lease
                expired = replace(
                    claim.state,
                    lease=replace(
                        lease,
                        acquired_at=lease.acquired_at - timedelta(seconds=61),
                        expires_at=lease.expires_at - timedelta(seconds=61),
                    ),
                    next_attempt_at=claim.state.next_attempt_at - timedelta(seconds=61),
                )
                async with admin.begin() as connection:
                    await connection.execute(
                        update(ci_history_rechecks)
                        .where(ci_history_rechecks.c.workflow_run_id == 302)
                        .values(**encode_history_recheck(expired))
                    )
            async with engine.connect() as connection:
                before = await load_history_recheck(connection, original.scope, 1, 302)
                assert before is not None and before.acquisition_count == 3
            assert await store.enqueue_recheck(_hint()) == "admitted"
            healthy = await store.claim_recheck(worker_id="b" * 64, source="recent")
            assert isinstance(healthy, HistoryRecheckClaim)
            assert healthy.state.hint.cursor.workflow_run_id == 303
            assert (
                await store.record_recheck_statistics(healthy, archived_statistics()) == "applied"
            )
            async with engine.connect() as connection:
                assert await load_history_recheck(connection, original.scope, 1, 302) == before
                assert await _counts(connection) == (1, 1, 1, 1)
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("with_detail", [False, True])
def test_queue_terminal_sql_expiry_rolls_back_every_contribution(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    with_detail: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        dataset = history_dataset()
        statistics = (
            archived_statistics()
            if with_detail
            else archived_statistics(population="partial", provider_total=2)
        )
        detail = archived_detail() if with_detail else None
        gap_count = int(not with_detail)
        try:
            async with admin.begin() as connection:
                await connection.execute(
                    insert(ci_history_datasets).values(**encode_history_dataset(dataset))
                )
            assert await store.enqueue_recheck(_hint()) == "admitted"
            original = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(original, HistoryRecheckClaim) and original.state.lease is not None
            shift = (
                original.state.lease.expires_at - await database_now(admin) - timedelta(seconds=4)
            )
            lease = replace(
                original.state.lease,
                acquired_at=original.state.lease.acquired_at - shift,
                expires_at=original.state.lease.expires_at - shift,
            )
            claim = replace(
                original,
                state=replace(
                    original.state,
                    lease=lease,
                    next_attempt_at=original.state.next_attempt_at - shift,
                ),
            )
            async with admin.begin() as connection:
                await connection.execute(
                    update(ci_history_rechecks).values(**encode_history_recheck(claim.state))
                )
            intercepted = False

            async def late_cas(
                connection: AsyncConnection,
                prior: HistoryRecheckState,
                successor: HistoryRecheckState | None,
                *,
                authority: RecheckCasAuthority,
            ) -> None:
                nonlocal intercepted
                intercepted = True
                assert prior == claim.state and authority == "holder"
                assert await _counts(connection) == (1, 1, gap_count, 1)
                changed = await load_history_dataset(connection, dataset.scope)
                assert (
                    changed is not None
                    and changed.usage.attempts == changed.usage.jobs == 1
                    and changed.usage.gaps == gap_count
                )
                children = (await connection.execute(select(ci_history_details))).mappings().all()
                assert len(children) == int(with_detail)
                if detail is not None:
                    assert children[0]["detail_canonical"] == encode_archive_detail(detail)
                await wait_for_database_deadline(admin, lease.expires_at)
                await write_history_recheck(connection, prior, successor, authority=authority)

            async with asyncio.timeout(12):
                with monkeypatch.context() as patch:
                    # noinspection PyUnresolvedReferences
                    patch.setattr(ci_history_recheck_completion, "write_history_recheck", late_cas)
                    assert (
                        await store.record_recheck_statistics(claim, statistics, detail=detail)
                        == "claim_lost"
                    )
            assert intercepted
            async with engine.connect() as connection:
                assert await _counts(connection) == (0, 0, 0, 1)
                assert not (await connection.execute(select(ci_history_details))).all()
                assert await load_history_dataset(connection, dataset.scope) == dataset
                assert await load_history_recheck(connection, dataset.scope, 1, 303) == claim.state
            fresh = await store.claim_recheck(worker_id="b" * 64, source="recent")
            assert (
                isinstance(fresh, HistoryRecheckClaim)
                and fresh.state.revision > claim.state.revision
            )
            assert await store.record_recheck_statistics(claim, statistics) == "claim_lost"
            assert (
                await store.record_recheck_statistics(fresh, statistics, detail=detail) == "applied"
            )
            async with engine.connect() as connection:
                assert await _counts(connection) == (1, 1, gap_count, 0)
                children = (await connection.execute(select(ci_history_details))).mappings().all()
                assert len(children) == int(with_detail)
                if detail is not None:
                    assert children[0]["detail_canonical"] == encode_archive_detail(detail)
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


async def _counts(connection: AsyncConnection) -> tuple[int, ...]:
    values: list[int] = []
    for table in (ci_history_attempts, ci_history_jobs, ci_history_gaps, ci_history_rechecks):
        value = await connection.scalar(select(func.count()).select_from(table))
        assert type(value) is int
        values.append(value)
    return tuple(values)


@pytest.mark.parametrize("latest", [1, 2])
def test_crashes_consume_budget_and_recovery_advances_once_with_durable_gap(
    runtime_postgres_database_url: str, postgres_database_url: str, latest: int
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(engine)
        dataset = history_dataset()
        hint = replace(_hint(), cursor=replace(_hint().cursor, latest_attempt=latest))
        claims: list[HistoryRecheckClaim] = []
        try:
            async with admin.begin() as connection:
                await connection.execute(
                    insert(ci_history_datasets).values(**encode_history_dataset(dataset))
                )
            assert await store.enqueue_recheck(hint) == "admitted"
            for count in (1, 2, 3):
                current = await store.claim_recheck(worker_id="a" * 64, source="recent")
                assert isinstance(current, HistoryRecheckClaim)
                assert current.state.acquisition_count == count and current.state.lease is not None
                assert current.state.next_attempt == 1
                claims.append(current)
                lease = current.state.lease
                expired = replace(
                    current.state,
                    lease=replace(
                        lease,
                        acquired_at=lease.acquired_at - timedelta(seconds=61),
                        expires_at=lease.expires_at - timedelta(seconds=61),
                    ),
                    next_attempt_at=current.state.next_attempt_at - timedelta(seconds=61),
                )
                async with admin.begin() as connection:
                    await connection.execute(
                        update(ci_history_rechecks).values(**encode_history_recheck(expired))
                    )
            assert await store.claim_recheck(worker_id="b" * 64, source="recent") == "recovered"
            for stale in claims:
                assert await store.record_recheck_failure(stale, missing=True) == "claim_lost"
            async with engine.connect() as connection:
                gap = await connection.scalar(select(ci_history_gaps.c.gap_canonical))
                assert isinstance(gap, bytes)
                payload = json.loads(gap)
                assert payload["reason"] == "retry_exhausted" and payload["runAttempt"] == 1
                assert payload["workflowRunId"] == 303 and payload["workflowId"] == 404
                state = await load_history_recheck(connection, dataset.scope, 1, 303)
                if latest == 1:
                    assert state is None
                else:
                    assert state is not None and state.next_attempt == 2
                    assert state.lease is None and state.acquisition_count == 0
                assert await _counts(connection) == (0, 0, 1, int(latest > 1))
            followup = await store.claim_recheck(worker_id="b" * 64, source="recent")
            if latest == 1:
                assert followup is None
            else:
                assert isinstance(followup, HistoryRecheckClaim)
                assert followup.state.next_attempt == 2 and followup.state.acquisition_count == 1
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())
