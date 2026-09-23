import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, insert, select, text, update
from tests.integration.persistence._ci_economics_support import database_now
from tests.integration.persistence._observation_support import (
    SCOPE,
    observation_command,
    observation_store,
    seed_configuration,
)

from ci_coordinator.ci_economics.observation_commands import ObservationCommitted
from ci_coordinator.ci_economics.observation_gaps import (
    MAX_OBSERVATION_GAPS,
    InvalidObservationGapCursor,
    ObservationGap,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_codec import encode_observation_payload
from ci_coordinator.persistence.ci_observation_gaps import record_observation_gap
from ci_coordinator.persistence.ci_observation_lock import lock_observation_scope
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_observation_gaps, ci_observation_subscriptions

pytestmark = pytest.mark.persistence


def test_expired_retained_anchor_cannot_resume_gap_history(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(admin)
            cycle = now - timedelta(days=91)
            async with admin.begin() as connection:
                snapshot = await seed_configuration(connection, SCOPE, cycle)
                gap = ObservationGap(
                    SCOPE,
                    1,
                    snapshot.configuration.selector_digest,
                    "recent",
                    cycle,
                    cycle,
                    cycle,
                    "provider_truncated",
                )
                await connection.execute(
                    insert(ci_observation_gaps).values(
                        gap_id=gap.gap_id,
                        installation_id=101,
                        repository_id=202,
                        gap_canonical=encode_observation_payload(gap.canonical_mapping()),
                        expires_at=gap.expires_at,
                    )
                )
            adapter = observation_store(engine)
            assert (await adapter.observation_gaps(SCOPE, after_cursor=None, limit=1)).gaps == ()
            with pytest.raises(InvalidObservationGapCursor):
                await adapter.observation_gaps(
                    SCOPE, after_cursor=f"101.202.1.{gap.gap_id}", limit=1
                )
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_gap_cap_eviction_replay_and_keyset_pages_preserve_incomplete_coverage(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        adapter = observation_store(engine)
        assert MAX_OBSERVATION_GAPS == 256
        try:
            now = await database_now(admin)
            cycle = now - timedelta(hours=1)
            async with admin.begin() as connection:
                snapshot = await seed_configuration(connection, SCOPE, now - timedelta(days=1))
            gaps = tuple(
                ObservationGap(
                    SCOPE,
                    1,
                    snapshot.configuration.selector_digest,
                    "backfill",
                    cycle,
                    cycle - timedelta(seconds=index + 1),
                    cycle - timedelta(seconds=index),
                    "provider_truncated",
                )
                for index in range(MAX_OBSERVATION_GAPS + 1)
            )
            async with engine.begin() as connection:
                await lock_observation_scope(connection, SCOPE)
                for gap in gaps:
                    await record_observation_gap(connection, gap, now)
            expected = sorted(gap.gap_id for gap in gaps)[1:]
            page = await adapter.observation_gaps(SCOPE, after_cursor=None, limit=50)
            assert tuple(gap.gap_id for gap in page.gaps) == tuple(expected[:50])
            assert page.next_cursor == f"101.202.1.{expected[49]}"
            seen = [gap.gap_id for gap in page.gaps]
            while page.next_cursor is not None:
                page = await adapter.observation_gaps(
                    SCOPE, after_cursor=page.next_cursor, limit=50
                )
                seen.extend(gap.gap_id for gap in page.gaps)
            assert seen == expected
            anchor = f"101.202.1.{expected[49]}"
            for invalid in (
                f"102.202.1.{expected[49]}",
                f"101.203.1.{expected[49]}",
                f"101.202.2.{expected[49]}",
                f"101.202.1.{min(gap.gap_id for gap in gaps)}",
                "101.202.1." + "f" * 64,
            ):
                with pytest.raises(InvalidObservationGapCursor):
                    await adapter.observation_gaps(SCOPE, after_cursor=invalid, limit=50)
            command = replace(observation_command(), expected_revision=1, operation_id="revise")
            assert isinstance(await adapter.configure_observation(command), ObservationCommitted)
            with pytest.raises(InvalidObservationGapCursor):
                await adapter.observation_gaps(SCOPE, after_cursor=anchor, limit=50)
            fresh = await adapter.observation_gaps(SCOPE, after_cursor=None, limit=50)
            assert fresh.next_cursor == f"101.202.2.{expected[49]}"
            continued = await adapter.observation_gaps(
                SCOPE, after_cursor=fresh.next_cursor, limit=50
            )
            assert [gap.gap_id for gap in continued.gaps] == expected[50:100]
            assert all(gap.config_revision == 1 for gap in continued.gaps)
            assert (await adapter.observation_status(SCOPE)).detail_truncated_until == gaps[
                0
            ].expires_at
            evicted = min(gaps, key=lambda gap: gap.gap_id)
            async with engine.begin() as connection:
                await lock_observation_scope(connection, SCOPE)
                await record_observation_gap(connection, evicted, now)
                await record_observation_gap(connection, max(gaps, key=lambda gap: gap.gap_id), now)
            assert (
                await adapter.observation_status(SCOPE)
            ).detail_truncated_until == evicted.expires_at
            async with engine.connect() as connection:
                rows = tuple(
                    await connection.scalars(
                        select(ci_observation_gaps.c.gap_id).order_by(
                            ci_observation_gaps.c.gap_id.collate("C")
                        )
                    )
                )
                assert rows == tuple(expected)
                assert (
                    await connection.scalar(select(func.min(ci_observation_gaps.c.expires_at)))
                    == evicted.expires_at
                )
            assert (
                await adapter.observation_gaps(
                    RepositoryScope(101, 203), after_cursor=None, limit=50
                )
            ).gaps == ()
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


def test_gap_cleanup_skips_a_locked_scope_and_eventually_removes_only_expired_evidence(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        adapter = observation_store(engine)
        try:
            now = await database_now(admin)
            scopes = tuple(RepositoryScope(101, repository) for repository in range(1, 7))
            retained: list[str] = []
            async with admin.begin() as connection:
                for scope in scopes:
                    snapshot = await seed_configuration(
                        connection, scope, now - timedelta(days=100)
                    )
                    for expired in (True, False):
                        cycle = now - timedelta(
                            days=91 if expired else 1, seconds=10 - scope.repository_id
                        )
                        gap = ObservationGap(
                            scope,
                            1,
                            snapshot.configuration.selector_digest,
                            "recent",
                            cycle,
                            cycle,
                            cycle,
                            "outage_window_lost",
                        )
                        await connection.execute(
                            insert(ci_observation_gaps).values(
                                gap_id=gap.gap_id,
                                installation_id=scope.installation_id,
                                repository_id=scope.repository_id,
                                gap_canonical=encode_observation_payload(gap.canonical_mapping()),
                                expires_at=gap.expires_at,
                            )
                        )
                        if not expired:
                            retained.append(gap.gap_id)
                    await connection.execute(
                        update(ci_observation_subscriptions)
                        .where(ci_observation_subscriptions.c.repository_id == scope.repository_id)
                        .values(
                            detail_truncated_until=now - timedelta(seconds=10 - scope.repository_id)
                        )
                    )
            async with admin.begin() as holder:
                await lock_observation_scope(holder, scopes[0])
                assert await adapter.purge_observation_gaps(scope_limit=4) == 4
                async with engine.connect() as connection:
                    pending = set(
                        await connection.scalars(
                            select(ci_observation_gaps.c.repository_id).where(
                                ci_observation_gaps.c.expires_at <= now
                            )
                        )
                    )
                    assert pending == {1, 6}
                assert await adapter.purge_observation_gaps(scope_limit=4) == 1
            assert await adapter.purge_observation_gaps(scope_limit=4) == 1
            assert await adapter.purge_observation_gaps(scope_limit=4) == 0
            async with engine.connect() as connection:
                assert set(await connection.scalars(select(ci_observation_gaps.c.gap_id))) == set(
                    retained
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(ci_observation_subscriptions)
                        .where(ci_observation_subscriptions.c.detail_truncated_until.is_not(None))
                    )
                    == 0
                )
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM ci_coordinator.ci_observation_scans")
                    )
                    == 12
                )
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
