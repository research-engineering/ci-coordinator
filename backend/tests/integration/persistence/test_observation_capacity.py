import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import event, func, select, text
from tests.integration.persistence._ci_economics_support import database_now
from tests.integration.persistence._observation_support import (
    observation_command,
    observation_store,
    seed_configuration,
)
from tests.integration.persistence._temporal_lock_support import wait_for_blocked_operation

from ci_coordinator.ci_economics.observation import MAX_OBSERVATION_REPOSITORIES
from ci_coordinator.ci_economics.observation_commands import (
    ObservationCommitted,
    ObservationConflict,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_observation_scans,
    ci_observation_subscriptions,
)

pytestmark = pytest.mark.persistence


def test_lock_observer_detects_a_session_opened_after_its_first_poll(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        late_engine = create_postgres_engine(postgres_database_url)
        first_poll = asyncio.Event()
        polls = 0

        def observed_poll(
            _connection: object, _cursor: object, statement: str, *_args: object
        ) -> None:
            nonlocal polls
            if "FROM pg_stat_activity" in statement and "pg_blocking_pids" in statement:
                polls += 1
                first_poll.set()

        async def late_contender() -> None:
            await first_poll.wait()
            async with late_engine.begin() as connection:
                await connection.execute(text("SELECT pg_advisory_xact_lock(7100156)"))

        event.listen(admin.sync_engine, "after_cursor_execute", observed_poll)
        try:
            async with asyncio.timeout(10), asyncio.TaskGroup() as group, admin.begin() as held:
                await held.execute(text("SELECT pg_advisory_xact_lock(7100156)"))
                holder = await held.scalar(select(func.pg_backend_pid()))
                assert type(holder) is int
                pending = group.create_task(late_contender())
                await wait_for_blocked_operation(admin, holder, pending)
                assert polls >= 2 and not pending.done()
                await held.commit()
            assert pending.done() and pending.result() is None
        finally:
            event.remove(admin.sync_engine, "after_cursor_execute", observed_poll)
            await late_engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_last_global_configuration_slot_is_linearized_without_blocking_updates_or_replay(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        adapter = observation_store(engine)
        maximum = MAX_OBSERVATION_REPOSITORIES
        assert maximum == 256
        try:
            now = await database_now(admin)
            async with admin.begin() as connection:
                for repository in range(1, maximum):
                    await seed_configuration(connection, RepositoryScope(101, repository), now)
            winner = observation_command(RepositoryScope(101, maximum))
            loser = observation_command(RepositoryScope(101, maximum + 1))
            async with (
                asyncio.timeout(15),
                asyncio.TaskGroup() as group,
                PostgresObservationUnitOfWork(engine) as transaction,
            ):
                result = await transaction.observation.configure_observation(winner)
                assert isinstance(result, ObservationCommitted)
                pid = await transaction.observation._connection.scalar(
                    select(func.pg_backend_pid())
                )
                assert type(pid) is int
                pending = group.create_task(adapter.configure_observation(loser))
                await wait_for_blocked_operation(admin, pid, pending)
                await transaction.commit()
            assert pending.result() == ObservationConflict("capacity_reached")
            assert await adapter.configure_observation(winner) == replace(result, replayed=True)
            paused = await adapter.configure_observation(
                replace(
                    winner,
                    expected_revision=1,
                    operation_id="pause",
                    configuration=replace(winner.configuration, enabled=False),
                )
            )
            assert isinstance(paused, ObservationCommitted) and paused.snapshot.revision == 2
            assert await adapter.configure_observation(winner) == replace(result, replayed=True)
            assert await adapter.configure_observation(loser) == ObservationConflict(
                "capacity_reached"
            )
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_observation_subscriptions)
                    )
                    == maximum
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_observation_scans))
                    == maximum * 2
                )
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 2
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
