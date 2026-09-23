import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from ci_coordinator.audit_replay import AuditAppendResult, PreparedAuditEvent
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_retention_commands import (
    ApplyHistoryRetention,
    HistoryRetentionRequest,
    HistoryRetentionSelection,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_attempts as attempts
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_details as details
from ci_coordinator.persistence._schema_ci_history_archive import ci_history_jobs as jobs
from ci_coordinator.persistence.ci_history_detail_cleanup import expire_history_details_in_scope
from ci_coordinator.persistence.ci_history_read_adapters import TransactionalHistoryReadStore
from ci_coordinator.persistence.ci_history_retention_store import apply_history_retention
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    lock_history_scope,
    write_history_dataset,
)
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import audit_events

from ._history_support import history_command, history_store
from .test_history_detail_cleanup import _seed_details

pytestmark = pytest.mark.persistence


def _selection(
    dataset: HistoryDataset, cutoff: str, **changes: object
) -> HistoryRetentionSelection:
    return HistoryRetentionSelection.model_validate(
        {
            "installationId": dataset.scope.installation_id,
            "repositoryId": dataset.scope.repository_id,
            "generation": dataset.generation,
            "configurationRevision": dataset.configuration_revision,
            "dataRevision": dataset.data_revision,
            "defaultRevision": 1,
            "importedThrough": cutoff,
            "action": "erase_details",
            "keys": [
                {"workflowRunId": 303, "runAttempt": 1},
                {"workflowRunId": 304, "runAttempt": 1},
            ],
            **changes,
        }
    )


def test_preview_and_explicit_erasure_preserve_statistics_and_replay_exact_receipt(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(admin, runtime, due=False)
            store = TransactionalHistoryReadStore(lambda: PostgresHistoryUnitOfWork(runtime))
            async with admin.connect() as connection:
                cutoff = (await history_database_time(connection)).isoformat()
                before = (
                    (
                        await connection.execute(
                            select(attempts).order_by(attempts.c.workflow_run_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                job_rows = (await connection.execute(select(jobs))).all()
                child_rows = (await connection.execute(select(details))).all()
            selection = _selection(original, cutoff)
            preview = await store.preview_retention(selection)
            assert preview is not None and preview.deleted_details == 2
            assert preview.released_bytes == sum(e.payload_bytes for e in preview.effects)
            async with admin.connect() as connection:
                assert (await connection.execute(select(details))).all() == child_rows
                assert await load_history_dataset(connection, original.scope) == original
            command = ApplyHistoryRetention.from_request(
                HistoryRetentionRequest(
                    selection=selection,
                    reviewedDigest=preview.review_digest,
                    operationId="erase-selected",
                ),
                actor="operator",
            )
            rejected = await store.apply_retention(
                command.model_copy(update={"reviewed_digest": "0" * 64})
            )
            assert rejected.outcome == "review_conflict" and rejected.preview is None
            receipt = await store.apply_retention(command)
            assert receipt.outcome == "committed" and receipt.preview == preview
            replay = await store.apply_retention(command)
            assert replay.outcome == "replayed" and replay.preview == preview
            assert replay.data_revision == original.data_revision + 1
            conflict = await store.apply_retention(command.model_copy(update={"actor": "other"}))
            assert conflict.outcome == "operation_conflict"
            async with admin.connect() as connection:
                after = (
                    (
                        await connection.execute(
                            select(attempts).order_by(attempts.c.workflow_run_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                for old, new in zip(before, after, strict=True):
                    assert {k: v for k, v in old.items() if k != "detail_state"} == {
                        k: v for k, v in new.items() if k != "detail_state"
                    }
                assert [r["detail_state"] for r in after] == ["expired", "expired", "retained"]
                assert (await connection.execute(select(jobs))).all() == job_rows
                assert (
                    await connection.execute(select(details.c.workflow_run_id))
                ).scalars().all() == [305]
                current = await load_history_dataset(connection, original.scope)
                assert current is not None and current.usage.attempts == original.usage.attempts
                assert (
                    current.usage.canonical_bytes
                    == original.usage.canonical_bytes - preview.released_bytes
                )
            assert await store.preview_retention(selection) is None
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("due", [False, True])
def test_apply_current_policy_does_not_renew_anchor_or_revive_expired_detail(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    due: bool,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            await _seed_details(admin, runtime, due=due)
            configured = await history_store(runtime).configure_history(
                ConfigureHistory.model_validate(
                    {
                        **history_command().model_dump(),
                        "expectedRevision": 1,
                        "initialCreatedFrom": None,
                        "operationId": "future-policy",
                    }
                )
            )
            assert isinstance(configured, HistoryConfigured)
            async with admin.connect() as connection:
                cutoff = (await history_database_time(connection)).isoformat()
                before = (
                    await connection.execute(
                        select(attempts.c.detail_first_imported_at).where(
                            attempts.c.workflow_run_id == 303
                        )
                    )
                ).scalar_one()
            store = TransactionalHistoryReadStore(lambda: PostgresHistoryUnitOfWork(runtime))
            selection = _selection(configured.snapshot, cutoff, action="apply_policy")
            preview = await store.preview_retention(selection)
            assert preview is not None
            assert preview.deleted_details == (2 if due else 0)
            for effect in preview.effects:
                assert effect.after.first_imported_at == effect.before.first_imported_at == before
                assert effect.after.state == ("expired" if due else "retained")
                assert effect.after.policy_revision == (1 if due else 2)
            receipt = await store.apply_retention(
                ApplyHistoryRetention(
                    selection=selection,
                    reviewedDigest=preview.review_digest,
                    operationId="apply-policy",
                    actor="operator",
                )
            )
            assert receipt.outcome == "committed"
            assert receipt.preview == preview
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["missing_child", "quota_underflow", "outer_rollback", "audit_append"]
)
def test_retention_failures_rollback_even_when_caught_inside_transaction(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(admin, runtime, due=False)
            store = TransactionalHistoryReadStore(lambda: PostgresHistoryUnitOfWork(runtime))
            async with admin.connect() as connection:
                cutoff = (await history_database_time(connection)).isoformat()
            selection = _selection(original, cutoff)
            preview = await store.preview_retention(selection)
            assert preview is not None
            command = ApplyHistoryRetention(
                selection=selection,
                reviewedDigest=preview.review_digest,
                operationId="rollback",
                actor="operator",
            )
            async with admin.begin() as connection:
                if failure == "missing_child":
                    await connection.execute(
                        delete(details).where(details.c.workflow_run_id == 304)
                    )
                elif failure == "quota_underflow":
                    await write_history_dataset(
                        connection,
                        original,
                        replace(
                            original, usage=original.usage.model_copy(update={"canonical_bytes": 0})
                        ),
                    )
                before = (await connection.execute(select(attempts))).all()
                children = (await connection.execute(select(details))).all()
                dataset_before = await load_history_dataset(connection, original.scope)
                audit_before = (await connection.execute(select(audit_events))).all()
            async with PostgresHistoryUnitOfWork(runtime) as transaction:
                if failure == "audit_append":
                    original_append = transaction.history_audit._append_pair_owned

                    async def fail_after_append(
                        event: PreparedAuditEvent, scope: RepositoryScope
                    ) -> AuditAppendResult:
                        await original_append(event, scope)
                        raise ValueError("audit append rollback witness")

                    monkeypatch.setattr(
                        transaction.history_audit, "_append_pair_owned", fail_after_append
                    )
                if failure == "outer_rollback":
                    receipt = await apply_history_retention(
                        transaction.history_connection, transaction.history_audit, command
                    )
                    assert receipt.outcome == "committed"
                else:
                    with pytest.raises(ValueError):
                        await apply_history_retention(
                            transaction.history_connection, transaction.history_audit, command
                        )
                    assert (
                        await transaction.history_connection.execute(select(attempts))
                    ).all() == before
                    assert (
                        await transaction.history_connection.execute(select(details))
                    ).all() == children
                    await transaction.commit()
            async with admin.connect() as connection:
                assert (await connection.execute(select(attempts))).all() == before
                assert (await connection.execute(select(details))).all() == children
                assert await load_history_dataset(connection, original.scope) == dataset_before
                assert (await connection.execute(select(audit_events))).all() == audit_before
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_cutoff_and_competing_cleanup_cannot_broaden_a_preview(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            original = await _seed_details(admin, runtime)
            store = TransactionalHistoryReadStore(lambda: PostgresHistoryUnitOfWork(runtime))
            async with admin.connect() as connection:
                now = await history_database_time(connection)
            assert (
                await store.preview_retention(
                    _selection(original, (now - timedelta(days=3)).isoformat())
                )
                is None
            )
            assert (
                await store.preview_retention(
                    _selection(original, (now + timedelta(days=1)).isoformat())
                )
                is None
            )
            selection = _selection(original, now.isoformat())
            preview = await store.preview_retention(selection)
            assert preview is not None
            async with runtime.begin() as holder:
                await lock_history_scope(holder, original.scope)
                async with runtime.begin() as competitor:
                    assert await expire_history_details_in_scope(competitor, original.scope) == 0
            async with runtime.begin() as cleaner:
                assert await expire_history_details_in_scope(cleaner, original.scope) == 3
            result = await store.apply_retention(
                ApplyHistoryRetention(
                    selection=selection,
                    reviewedDigest=preview.review_digest,
                    operationId="stale-preview",
                    actor="operator",
                )
            )
            assert result.outcome == "revision_conflict" and result.preview is None
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())
