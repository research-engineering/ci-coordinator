import asyncio

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationConflict,
    HistoryConfigurationInvalid,
    HistoryConfigured,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets,
    ci_history_scans,
)
from ci_coordinator.persistence.ci_history_state_store import (
    load_history_dataset,
    load_history_scan,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import audit_events

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence


def test_future_initial_population_is_invalid_without_persisting_audit_or_configuration(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        command = ConfigureHistory.model_validate(
            {**history_command().model_dump(), "initialCreatedFrom": "9999-01-01T00:00:00Z"}
        )
        try:
            assert (
                await history_store(engine).configure_history(command)
                == HistoryConfigurationInvalid()
            )
            async with engine.connect() as connection:
                for table in (audit_events, ci_history_datasets, ci_history_scans):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_configuration_replay_cannot_rewind_a_pause_or_restore_the_old_claim(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = history_store(engine)
        original = history_command()
        try:
            first = await adapter.configure_history(original)
            assert isinstance(first, HistoryConfigured) and not first.replayed
            acquired = await adapter.claim_history(worker_id="a" * 64)
            assert acquired is not None
            pause = ConfigureHistory.model_validate(
                {
                    **original.model_dump(),
                    "expectedRevision": 1,
                    "initialCreatedFrom": None,
                    "operationId": "pause-history",
                    "configuration": {**original.configuration.model_dump(), "enabled": False},
                }
            )
            paused = await adapter.configure_history(pause)
            assert isinstance(paused, HistoryConfigured) and paused.snapshot.state == "paused"
            assert await adapter.configure_history(original) == HistoryConfigured(
                first.snapshot, replayed=True
            )
            assert await adapter.claim_history(worker_id="a" * 64) is None
            assert await adapter.defer_history(acquired[1], "provider_unavailable") == "claim_lost"
            for patch, reason in [
                ({"operationId": "stale-operation"}, "revision_conflict"),
                ({"actor": "operator-2"}, "operation_conflict"),
            ]:
                result = await adapter.configure_history(
                    ConfigureHistory.model_validate({**original.model_dump(), **patch})
                )
                assert isinstance(result, HistoryConfigurationConflict) and result.reason == reason
            async with engine.connect() as connection:
                dataset = await load_history_dataset(connection, original.scope)
                scan = await load_history_scan(connection, original.scope)
                assert dataset == paused.snapshot
                assert scan is not None and scan.lease is None
                assert scan.configuration_revision == paused.snapshot.configuration_revision
                assert scan.checkpoint == acquired[1].state.checkpoint
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 2
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_datasets))
                    == 1
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_scans)) == 2
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("relation", ["audit_events", "ci_history_datasets", "ci_history_scans"])
def test_configuration_failure_rolls_back_audit_dataset_and_scan_together(
    runtime_postgres_database_url: str,
    relation: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = history_store(engine)
        injected = False

        def reject_write(
            _connection: object, _cursor: object, statement: str, *_args: object
        ) -> None:
            nonlocal injected
            if statement.startswith(f"INSERT INTO ci_coordinator.{relation} "):
                injected = True
                raise SQLAlchemyError("injected history configuration failure")

        try:
            event.listen(engine.sync_engine, "after_cursor_execute", reject_write)
            try:
                with pytest.raises(CiEconomicsStoreUnavailable):
                    await adapter.configure_history(history_command())
            finally:
                event.remove(engine.sync_engine, "after_cursor_execute", reject_write)
            assert injected
            async with engine.connect() as connection:
                for table in (audit_events, ci_history_datasets, ci_history_scans):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
            result = await adapter.configure_history(history_command())
            assert isinstance(result, HistoryConfigured) and not result.replayed
            assert result.snapshot.configuration_revision == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
