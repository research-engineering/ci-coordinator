import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store

from ci_coordinator.ci_economics.observation import ObservationConfiguration
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
    ObservationConflict,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_adapters import TransactionalObservationStore
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_observation_scans,
    ci_observation_subscriptions,
)

pytestmark = pytest.mark.persistence
_SCOPE = RepositoryScope(101, 202)


def _command() -> ConfigureObservation:
    return ConfigureObservation(
        _SCOPE, 0, ObservationConfiguration(True, (10, 20), 1), "enable-observation", "operator-1"
    )


def test_unconfigured_status_includes_manual_sources_and_configuration_replay_cannot_rewind_pause(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = TransactionalObservationStore(lambda: PostgresObservationUnitOfWork(engine))
        try:
            source = provider_source(501, await database_now(engine))
            assert await store(engine).register_provider_source(source) == "registered"
            unconfigured = await adapter.observation_status(_SCOPE)
            assert unconfigured.snapshot is None and unconfigured.scans == ()
            assert unconfigured.occupied_source_slots == 1
            original = _command()
            first = await adapter.configure_observation(original)
            assert isinstance(first, ObservationCommitted) and not first.replayed
            assert first.snapshot.revision == 1
            status = await adapter.observation_status(_SCOPE)
            assert status.snapshot == first.snapshot
            assert {scan.state.lane for scan in status.scans} == {"recent", "backfill"}
            assert all(scan.pages_seen == scan.sources_registered == 0 for scan in status.scans)
            assert status.occupied_source_slots == 1
            pause = replace(
                original,
                expected_revision=1,
                configuration=replace(original.configuration, enabled=False),
                operation_id="pause-observation",
            )
            paused = await adapter.configure_observation(pause)
            assert isinstance(paused, ObservationCommitted) and paused.snapshot.revision == 2
            replay = await adapter.configure_observation(original)
            assert replay == ObservationCommitted(first.snapshot, replayed=True)
            assert (await adapter.observation_status(_SCOPE)).snapshot == paused.snapshot
            assert await adapter.claim_observation(worker_id="a" * 64) is None
            assert await adapter.configure_observation(
                replace(original, operation_id="stale-operation")
            ) == ObservationConflict("revision_conflict")
            assert await adapter.configure_observation(
                replace(original, actor="different-operator")
            ) == ObservationConflict("operation_conflict")
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 2
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_observation_scans))
                    == 2
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "relation",
    [
        "audit_events",
        "ci_observation_subscriptions",
        "ci_observation_scans",
    ],
)
def test_configuration_failure_rolls_back_audit_subscription_and_scans_together(
    runtime_postgres_database_url: str, relation: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = TransactionalObservationStore(lambda: PostgresObservationUnitOfWork(engine))
        injected = False

        def reject_write(
            _connection: object, _cursor: object, statement: str, *_args: object
        ) -> None:
            nonlocal injected
            if statement.startswith(f"INSERT INTO ci_coordinator.{relation} "):
                injected = True
                raise SQLAlchemyError("injected observation write failure")

        try:
            event.listen(engine.sync_engine, "after_cursor_execute", reject_write)
            try:
                with pytest.raises(CiEconomicsStoreUnavailable):
                    await adapter.configure_observation(_command())
            finally:
                event.remove(engine.sync_engine, "after_cursor_execute", reject_write)
            assert injected
            async with engine.connect() as connection:
                for table in (audit_events, ci_observation_subscriptions, ci_observation_scans):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
            assert (await adapter.observation_status(_SCOPE)).snapshot is None
            successful = await adapter.configure_observation(_command())
            assert isinstance(successful, ObservationCommitted) and not successful.replayed
            assert successful.snapshot.revision == 1
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
