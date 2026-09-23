import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from unittest.mock import Mock

import pytest
from sqlalchemy import DateTime, func, literal, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration.persistence._ci_economics_support import database_now, provider_source
from tests.integration.persistence._observation_support import (
    SCOPE,
    observation_command,
    observation_store,
    seed_configuration,
)
from tests.integration.persistence._temporal_lock_support import (
    wait_for_blocked_operation,
    wait_for_database_deadline,
)

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.observation_commands import ObservationCommitted
from ci_coordinator.ci_economics.observation_scan import ObservationClaim
from ci_coordinator.persistence import ci_observation_claims, ci_observation_completion
from ci_coordinator.persistence import ci_observation_scan_state as scan_storage
from ci_coordinator.persistence.ci_observation_codec import decode_scan, encode_scan
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_observation_scans, ci_workflow_attempt_collections

pytestmark = pytest.mark.persistence


def _page(claim: ObservationClaim) -> ProviderObservationPage:
    return ProviderObservationPage(
        ProviderRunDiscoveryPage(
            SCOPE,
            claim.cursor.window,
            claim.cursor.page_number,
            1,
            (
                provider_source(
                    11 if claim.lane == "recent" else 22, claim.cursor.window.created_from
                ),
            ),
            "exhausted",
        ),
        (10,),
    )


@pytest.mark.parametrize("pause_first", [False, True])
def test_pause_and_page_compete_at_real_database_lock_boundary(
    runtime_postgres_database_url: str, postgres_database_url: str, pause_first: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        observer = create_postgres_engine(postgres_database_url)
        adapter = observation_store(engine)
        try:
            command = observation_command()
            assert isinstance(await adapter.configure_observation(command), ObservationCommitted)
            claimed = await adapter.claim_observation(worker_id="a" * 64)
            assert claimed is not None
            pause = replace(
                command,
                expected_revision=1,
                operation_id="pause",
                configuration=replace(command.configuration, enabled=False),
            )
            async with (
                asyncio.timeout(15),
                asyncio.TaskGroup() as group,
                PostgresObservationUnitOfWork(engine) as first,
            ):
                connection = first.observation._connection
                pid = await connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                second: asyncio.Task[object]
                if pause_first:
                    assert isinstance(
                        await first.observation.configure_observation(pause), ObservationCommitted
                    )
                    second = group.create_task(
                        adapter.record_observation_page(claimed.claim, _page(claimed.claim))
                    )
                else:
                    assert (
                        await first.observation.record_observation_page(
                            claimed.claim, _page(claimed.claim)
                        )
                        == "applied"
                    )
                    second = group.create_task(adapter.configure_observation(pause))
                await wait_for_blocked_operation(observer, pid, second)
                await first.commit()
            if pause_first:
                assert second.result() == "claim_lost"
            else:
                assert isinstance(second.result(), ObservationCommitted)
            status = await adapter.observation_status(SCOPE)
            assert status.snapshot is not None and not status.snapshot.configuration.enabled
            assert await adapter.claim_observation(worker_id="b" * 64) is None
            async with engine.connect() as connection:
                assert await connection.scalar(
                    select(func.count()).select_from(ci_workflow_attempt_collections)
                ) == int(not pause_first)
        finally:
            await observer.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["acquire", "page", "defer"])
@pytest.mark.parametrize("sample_shift_seconds", [-120, 120])
def test_final_cas_rejects_independently_stale_domain_time(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    operation: Literal["acquire", "page", "defer"],
    sample_shift_seconds: int,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        adapter = observation_store(engine)
        try:
            async with admin.begin() as connection:
                await seed_configuration(
                    connection, SCOPE, await database_now(admin) - timedelta(minutes=10)
                )
            domain_now = await database_now(admin) + timedelta(seconds=sample_shift_seconds)
            claim = None
            if operation != "acquire":
                claimed = await adapter.claim_observation(worker_id="a" * 64)
                assert claimed is not None
                async with admin.begin() as connection:
                    row = (
                        (
                            await connection.execute(
                                select(ci_observation_scans).where(
                                    ci_observation_scans.c.lane == claimed.claim.lane
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    prior = decode_scan(row)
                    lease = replace(
                        claimed.claim.lease,
                        acquired_at=domain_now,
                        expires_at=domain_now + timedelta(seconds=60),
                    )
                    await connection.execute(
                        update(ci_observation_scans)
                        .where(ci_observation_scans.c.lane == claimed.claim.lane)
                        .values(
                            **encode_scan(replace(prior, state=replace(prior.state, lease=lease)))
                        )
                    )
                claim = replace(claimed.claim, lease=lease)
            before = await adapter.observation_status(SCOPE)
            samples = 0

            async def stale_sample(_connection: AsyncConnection) -> datetime:
                nonlocal samples
                samples += 1
                return domain_now

            owner = ci_observation_claims if operation == "acquire" else ci_observation_completion
            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(owner, "observation_database_time", stale_sample)
                if operation == "acquire":
                    assert await adapter.claim_observation(worker_id="a" * 64) is None
                else:
                    assert claim is not None
                    result = (
                        await adapter.record_observation_page(claim, _page(claim))
                        if operation == "page"
                        else await adapter.defer_observation(claim, "provider_unavailable")
                    )
                    assert result == "claim_lost"
            assert samples > 0
            after = await adapter.observation_status(SCOPE)
            assert after.snapshot == before.snapshot and after.scans == before.scans
            assert after.occupied_source_slots == before.occupied_source_slots == 0
            gaps = await adapter.observation_gaps(SCOPE, after_cursor=None, limit=50)
            assert gaps.gaps == ()
            if operation == "acquire":
                assert await adapter.claim_observation(worker_id="b" * 64) is not None
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "offset,allowed",
    [(-1, False), (0, True), (59_999_999, True), (60_000_000, False), (60_000_001, False)],
)
def test_postgres_cas_interval_at_controlled_clock_boundaries(
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
    allowed: bool,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = observation_store(engine)
        try:
            assert isinstance(
                await adapter.configure_observation(observation_command()), ObservationCommitted
            )
            claimed = await adapter.claim_observation(worker_id="a" * 64)
            assert claimed is not None
            claim = claimed.claim
            observed_time = claim.lease.acquired_at + timedelta(microseconds=offset)
            clock = Mock(return_value=literal(observed_time, DateTime(timezone=True)))
            async with engine.begin() as connection:
                prior = decode_scan(
                    (
                        await connection.execute(
                            select(ci_observation_scans)
                            .where(ci_observation_scans.c.lane == claim.lane)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                successor = replace(
                    prior, state=replace(prior.state, revision=prior.state.revision + 1, lease=None)
                )
                with monkeypatch.context() as patch:
                    # noinspection PyUnresolvedReferences
                    patch.setattr(
                        scan_storage,
                        "func",
                        SimpleNamespace(tstzrange=func.tstzrange, clock_timestamp=clock),
                    )
                    if allowed:
                        await scan_storage.write_observation_scan(
                            connection, prior.state, successor, live_lease=claim.lease
                        )
                    else:
                        with pytest.raises(scan_storage.ObservationLeaseExpired):
                            await scan_storage.write_observation_scan(
                                connection, prior.state, successor, live_lease=claim.lease
                            )
                clock.assert_called_once_with()
                actual = decode_scan(
                    (
                        await connection.execute(
                            select(ci_observation_scans).where(
                                ci_observation_scans.c.lane == claim.lane
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert actual == (successor if allowed else prior)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_source_quota_wait_past_lease_expiry_rolls_back_every_page_write(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        adapter = observation_store(engine)
        try:
            async with admin.begin() as connection:
                await seed_configuration(
                    connection, SCOPE, await database_now(admin) - timedelta(minutes=2)
                )
            claimed = await adapter.claim_observation(worker_id="a" * 64)
            assert claimed is not None
            async with admin.begin() as connection:
                row = (
                    (
                        await connection.execute(
                            select(ci_observation_scans).where(
                                ci_observation_scans.c.lane == claimed.claim.lane
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                prior = decode_scan(row)
                assert prior.state.lease is not None
                shift = (
                    prior.state.lease.expires_at - await database_now(admin) - timedelta(seconds=4)
                )
                lease = replace(
                    prior.state.lease,
                    acquired_at=prior.state.lease.acquired_at - shift,
                    expires_at=prior.state.lease.expires_at - shift,
                )
                state = replace(prior.state, lease=lease)
                await connection.execute(
                    update(ci_observation_scans)
                    .where(ci_observation_scans.c.lane == state.lane)
                    .values(**encode_scan(replace(prior, state=state)))
                )
            claim = replace(claimed.claim, lease=lease)
            before = await adapter.observation_status(SCOPE)
            async with asyncio.timeout(12), asyncio.TaskGroup() as group:
                async with admin.begin() as holder:
                    pid = await holder.scalar(select(func.pg_backend_pid()))
                    assert type(pid) is int
                    await holder.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": "ci-economics-source-quota/v1:101:202"},
                    )
                    operation = group.create_task(
                        adapter.record_observation_page(claim, _page(claim))
                    )
                    await wait_for_blocked_operation(admin, pid, operation)
                    await wait_for_database_deadline(admin, lease.expires_at)
                assert await operation == "claim_lost"
            after = await adapter.observation_status(SCOPE)
            assert after.scans == before.scans
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_collections)
                    )
                    == 0
                )
            successor = await adapter.claim_observation(worker_id="b" * 64)
            assert successor is not None and successor.claim.lease.token != claim.lease.token
            assert (
                await adapter.record_observation_page(successor.claim, _page(successor.claim))
                == "applied"
            )
            reclaimed = await adapter.claim_observation(worker_id="c" * 64)
            assert reclaimed is not None and reclaimed.claim.lane == claim.lane
            assert reclaimed.claim.expected_revision == claim.expected_revision + 1
            assert reclaimed.claim.lease.token != claim.lease.token
            assert await adapter.record_observation_page(claim, _page(claim)) == "claim_lost"
            assert (
                await adapter.record_observation_page(reclaimed.claim, _page(reclaimed.claim))
                == "applied"
            )
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
