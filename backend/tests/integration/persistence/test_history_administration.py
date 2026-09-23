import asyncio
from dataclasses import replace

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME
from sqlalchemy import delete, event, insert, select, update

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration
from ci_coordinator.ci_economics.history_lifecycle import configure_history_state
from ci_coordinator.ci_economics.history_recent import configure_recent_history
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_RUNS,
    HistoryRecheckHint,
    HistoryRecheckState,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_history_control_codec import encode_history_scan
from ci_coordinator.persistence.ci_history_recheck_codec import encode_history_recheck
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    lock_history_scope,
    write_history_dataset,
    write_history_scan,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_history_defaults,
    ci_history_rechecks,
    ci_history_scans,
)

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence


def test_status_is_one_nonlocking_snapshot_during_a_concurrent_configuration(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store, command = history_store(engine), history_command()
        statements: list[str] = []

        def observe(_connection: object, _cursor: object, statement: str, *_args: object) -> None:
            statements.append(statement)

        try:
            unconfigured = await store.history_status(command.scope)
            assert unconfigured.dataset is None and unconfigured.scan is None
            assert unconfigured.pending_rechecks == 0 and unconfigured.defaults.revision == 1
            configured = await store.configure_history(command)
            assert isinstance(configured, HistoryConfigured)
            prior = await store.history_status(command.scope)
            assert prior.dataset == configured.snapshot and prior.scan is not None
            assert prior.dataset is not None
            async with engine.begin() as writer:
                assert await lock_history_scope(writer, command.scope)
                successor, scan = configure_history_state(
                    command.scope,
                    HistoryConfiguration.model_validate(
                        {**command.configuration.model_dump(), "enabled": False}
                    ),
                    prior=prior.dataset,
                    scan=prior.scan,
                    now=await history_database_time(writer),
                )
                await write_history_dataset(writer, prior.dataset, successor)
                await write_history_scan(writer, prior.scan, scan)
                assert prior.discovery is not None
                discovery = configure_recent_history(successor, scan, prior=prior.discovery)
                await write_history_scan(writer, prior.discovery, discovery)
                event.listen(engine.sync_engine, "before_cursor_execute", observe)
                try:
                    async with asyncio.timeout(10):
                        concurrent = await store.history_status(command.scope)
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", observe)
                assert concurrent.dataset == prior.dataset and concurrent.scan == prior.scan
                assert concurrent.observed_at >= prior.observed_at
                status_reads = [
                    sql
                    for sql in statements
                    if " AS defaults_revision" in sql and " AS pending_rechecks" in sql
                ]
                assert len(status_reads) == 1
                assert "LIMIT" in status_reads[0]
                assert "FOR UPDATE" not in status_reads[0] and "advisory" not in status_reads[0]
                assert all(
                    not sql.startswith(("INSERT ", "UPDATE ", "DELETE ")) for sql in statements
                )
            current = await store.history_status(command.scope)
            assert current.dataset == successor and current.scan == scan
            assert current.discovery == discovery
            foreign = await store.history_status(RepositoryScope(command.installation_id, 999))
            assert (
                foreign.dataset is None and foreign.scan is None and foreign.pending_rechecks == 0
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_pause_rescan_and_old_replay_leave_status_at_the_latest_committed_revision(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store, command = history_store(engine), history_command()
        try:
            original = await store.configure_history(command)
            assert isinstance(original, HistoryConfigured)
            for revision, enabled, rescan in ((1, False, False), (2, True, True)):
                changed = ConfigureHistory.model_validate(
                    {
                        **command.model_dump(),
                        "expectedRevision": revision,
                        "initialCreatedFrom": None,
                        "operationId": f"configure-{revision}",
                        "rescan": rescan,
                        "configuration": {**command.configuration.model_dump(), "enabled": enabled},
                    }
                )
                result = await store.configure_history(changed)
                assert isinstance(result, HistoryConfigured)
                status = await store.history_status(command.scope)
                assert status.dataset == result.snapshot and status.scan is not None
                assert status.scan.configuration_revision == revision + 1
                assert (
                    status.scan.lease is None
                    and status.scan.checkpoint.cursor.created_from == ARCHIVE_TIME
                )
                assert status.dataset.state == ("active" if enabled else "paused")
            assert await store.configure_history(command) == HistoryConfigured(
                original.snapshot, True
            )
            after_replay = await store.history_status(command.scope)
            assert after_replay.dataset == status.dataset and after_replay.scan == status.scan
            async with engine.connect() as connection:
                assert (
                    len((await connection.execute(select(audit_events.c.audit_event_id))).all())
                    == 3
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recheck_count_is_generation_scoped_and_rejects_overflow_without_truncating(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store, command = history_store(runtime), history_command()
        try:
            result = await store.configure_history(command)
            assert isinstance(result, HistoryConfigured)
            initial = HistoryRecheckState(
                HistoryRecheckHint(
                    HistoryAttemptCursor(command.scope, 1, 1), 404, ARCHIVE_TIME, "recent"
                ),
                1,
                1,
                1,
                result.snapshot.configured_at,
            )
            rows = [
                encode_history_recheck(
                    replace(
                        initial,
                        hint=replace(
                            initial.hint, cursor=HistoryAttemptCursor(command.scope, run_id, 1)
                        ),
                    )
                )
                for run_id in range(1, MAX_HISTORY_RECHECK_RUNS + 2)
            ]
            async with admin.begin() as connection:
                await connection.execute(insert(ci_history_rechecks), rows[:-1])
                await connection.execute(
                    insert(ci_history_rechecks),
                    encode_history_recheck(replace(initial, generation=2)),
                )
            assert (
                await store.history_status(command.scope)
            ).pending_rechecks == MAX_HISTORY_RECHECK_RUNS
            async with admin.begin() as connection:
                await connection.execute(insert(ci_history_rechecks), rows[-1])
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.history_status(command.scope)
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["defaults", "scan", "revision"])
def test_missing_or_contradictory_status_authority_is_unavailable_not_empty(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    damage: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store, command = history_store(runtime), history_command()
        try:
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            status = await store.history_status(command.scope)
            assert status.scan is not None
            async with admin.begin() as connection:
                if damage in {"defaults", "scan"}:
                    await connection.execute(
                        delete(ci_history_defaults if damage == "defaults" else ci_history_scans)
                    )
                else:
                    await connection.execute(
                        update(ci_history_scans)
                        .where(ci_history_scans.c.lane == "backfill")
                        .values(
                            **encode_history_scan(replace(status.scan, configuration_revision=2))
                        )
                    )
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.history_status(command.scope)
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
