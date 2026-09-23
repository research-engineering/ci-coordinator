import asyncio
from typing import Literal

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_rechecks import HistoryRecheckClaim, HistoryRecheckHint
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.persistence.ci_history_state_store import load_history_dataset
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_history_attempts,
    ci_history_jobs,
    ci_history_rechecks,
)

from ._history_support import history_command, history_store

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("change", ["narrow", "all", "pause", "audit_failure"])
def test_configuration_retires_only_excluded_queue_work_and_preserves_statistics(
    runtime_postgres_database_url: str,
    change: Literal["narrow", "all", "pause", "audit_failure"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        baseline = history_command()
        original = ConfigureHistory.model_validate(
            {
                **baseline.model_dump(),
                "configuration": {**baseline.configuration.model_dump(), "workflowIds": [404, 405]},
            }
        )
        deleted = False
        rejected = False

        def reject_audit(
            _connection: object, _cursor: object, statement: str, *_args: object
        ) -> None:
            nonlocal deleted, rejected
            if statement.startswith("DELETE FROM ci_coordinator.ci_history_rechecks "):
                deleted = True
            if statement.startswith("INSERT INTO ci_coordinator.audit_events "):
                assert deleted
                rejected = True
                raise SQLAlchemyError("injected history queue configuration failure")

        try:
            configured = await store.configure_history(original)
            assert isinstance(configured, HistoryConfigured)
            first = HistoryRecheckHint(
                HistoryAttemptCursor(original.scope, 303, 1), 404, ARCHIVE_TIME, "recent"
            )
            assert await store.enqueue_recheck(first) == "admitted"
            imported = await store.claim_recheck(worker_id="a" * 64, source="recent")
            assert isinstance(imported, HistoryRecheckClaim)
            assert (
                await store.record_recheck_statistics(imported, archived_statistics()) == "applied"
            )
            assert (
                await store.enqueue_recheck(
                    HistoryRecheckHint(
                        HistoryAttemptCursor(original.scope, 304, 1), 405, ARCHIVE_TIME, "recent"
                    )
                )
                == "admitted"
            )
            live = await store.claim_recheck(worker_id="b" * 64, source="recent")
            assert isinstance(live, HistoryRecheckClaim) and live.state.hint.workflow_id == 405
            assert await store.enqueue_recheck(first) == "admitted"
            async with engine.connect() as connection:
                before = await load_history_dataset(connection, original.scope)
                assert before is not None
                prior_rows = (
                    (await connection.execute(select(ci_history_rechecks))).mappings().all()
                )
            selector = None if change == "all" else [404, 405] if change == "pause" else [404]
            command = ConfigureHistory.model_validate(
                {
                    **original.model_dump(),
                    "expectedRevision": 1,
                    "initialCreatedFrom": None,
                    "operationId": "change-history-selector",
                    "configuration": {
                        **original.configuration.model_dump(),
                        "workflowIds": selector,
                        "enabled": change != "pause",
                    },
                }
            )
            if change == "audit_failure":
                event.listen(engine.sync_engine, "after_cursor_execute", reject_audit)
                try:
                    with pytest.raises(CiEconomicsStoreUnavailable):
                        await store.configure_history(command)
                finally:
                    event.remove(engine.sync_engine, "after_cursor_execute", reject_audit)
                assert deleted and rejected
            else:
                result = await store.configure_history(command)
                assert isinstance(result, HistoryConfigured) and not result.replayed
                assert await store.configure_history(command) == HistoryConfigured(
                    result.snapshot, replayed=True
                )
                assert await store.record_recheck_failure(live, missing=True) == "claim_lost"
            async with engine.connect() as connection:
                rows = (await connection.execute(select(ci_history_rechecks))).mappings().all()
                expected = {303} if change == "narrow" else {303, 304}
                assert {row["workflow_run_id"] for row in rows} == expected
                if change == "audit_failure":
                    assert rows == prior_rows
                    assert await load_history_dataset(connection, original.scope) == before
                for table in (ci_history_attempts, ci_history_jobs):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 1
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == (
                    1 if change == "audit_failure" else 2
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
