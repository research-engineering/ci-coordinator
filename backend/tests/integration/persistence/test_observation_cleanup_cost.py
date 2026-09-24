import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from time import perf_counter_ns
from typing import Literal

import pytest
from ci_economics.budget_factories import budget_command
from ci_economics.report_factories import measurement_report
from sqlalchemy import Select, event, func, select, text
from sqlalchemy.pool import AsyncAdaptedQueuePool
from tests.integration.persistence._ci_economics_support import (
    database_now,
    snapshot,
    store,
    subject,
)
from tests.integration.persistence._economics_retention_support import shift_retention_epoch
from tests.integration.persistence._observation_cleanup_support import (
    expand_cleanup_snapshot,
    seed_cleanup_attempt,
)

from ci_coordinator.ci_economics import load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted
from ci_coordinator.ci_economics.read_models import IndependentAttemptSnapshot
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_budget_adapters import TransactionalBudgetPolicyStore
from ci_coordinator.persistence.ci_economics_collection_codec import decode_collection_source
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import PersistenceInvariantViolation
from ci_coordinator.persistence.schema import (
    ci_economics_budget_signals,
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
)

pytestmark = pytest.mark.persistence


def test_maximum_cleanup_batch_preserves_live_children_and_finishes_within_profile_budget(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        profile = load_bundled_ci_economics_profile()
        assert profile.cleanup_batch_size == 100 and profile.maximum_jobs_per_attempt == 2000
        collection = store(engine)
        budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
        live_source: ProviderRunCollectionSource | None = None
        try:
            now = await database_now(engine)
            preparation_started = perf_counter_ns()
            committed_seed_elapsed = 0
            expansion_elapsed = 0
            expansion_construction_elapsed = 0
            expansion_transaction_elapsed = 0
            retention_shift_elapsed = 0
            async with asyncio.timeout(180):
                for index in range(101):
                    prototype = snapshot(subject(1000 + index), now)
                    source = ProviderRunCollectionSource(
                        prototype.attempt, now, "2026-03-10", "a" * 64
                    )
                    evidence = replace(prototype, subject_id=source.source_id)
                    report = replace(
                        measurement_report(attempt=source.attempt),
                        provider_job_id=evidence.jobs[0].provider_job_id,
                        reported_at=now,
                    )
                    if index == 0:
                        assert isinstance(
                            await budgets.configure_policy(budget_command(report)),
                            BudgetPolicyCommitted,
                        )
                    seed_started = perf_counter_ns()
                    await seed_cleanup_attempt(engine, source, evidence, report)
                    committed_seed_elapsed += perf_counter_ns() - seed_started
                    if index < 100:
                        expansion_started = perf_counter_ns()
                        digest, construction, write_duration = await expand_cleanup_snapshot(
                            admin, evidence, 2000
                        )
                        expansion_elapsed += perf_counter_ns() - expansion_started
                        expansion_construction_elapsed += construction
                        expansion_transaction_elapsed += write_duration
                        if index == 0:
                            decoded = await collection.load_measurements(source.attempt)
                            assert decoded is not None
                            assert isinstance(decoded.snapshot, IndependentAttemptSnapshot)
                            restored = decoded.snapshot.evidence
                            assert restored.snapshot_digest == digest
                            assert len(restored.jobs) == 2000
                            assert restored.jobs[0].provider_job_id == 1
                            assert restored.jobs[-1].provider_job_id == 2000
                        shift_started = perf_counter_ns()
                        await shift_retention_epoch(
                            admin, source.source_id, timedelta(days=90, hours=1)
                        )
                        retention_shift_elapsed += perf_counter_ns() - shift_started
                    else:
                        live_source = source
            preparation_elapsed = perf_counter_ns() - preparation_started
            assert live_source is not None
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_snapshot_jobs)
                    )
                    == 200_001
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_job_measurement_reports)
                    )
                    == 101
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_economics_budget_signals)
                    )
                    == 101
                )
                assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
            statements: list[str] = []
            selection: list[str] = []
            capture_only = True

            class SelectionCaptured(Exception):
                pass

            def capture(
                _connection: object, _cursor: object, statement: str, *_arguments: object
            ) -> None:
                statements.append(statement)

            def capture_selection(
                _connection: object, statement: object, *_arguments: object
            ) -> None:
                if isinstance(statement, Select) and statement.selected_columns.contains_column(
                    ci_workflow_attempt_collections.c.subject_id
                ):
                    selection.append(
                        str(
                            statement.compile(
                                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                            )
                        )
                    )
                    if capture_only:
                        raise SelectionCaptured

            event.listen(engine.sync_engine, "before_execute", capture_selection)
            try:
                with pytest.raises(SelectionCaptured):
                    async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                        await transaction.ci_economics_collection.expire_evidence(limit=100)
            finally:
                event.remove(engine.sync_engine, "before_execute", capture_selection)
            assert len(selection) == 1
            capture_only = False
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_snapshot_jobs)
                    )
                    == 200_001
                )
                plan = await connection.scalar(
                    text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + selection[0])
                )
                assert isinstance(plan, list) and len(plan) == 1
                assert plan[0]["Plan"]["Actual Rows"] == 100
            selected_query = selection.pop()
            async with (
                asyncio.timeout(profile.expiry_deadline_seconds),
                PostgresCiEconomicsUnitOfWork(engine) as transaction,
            ):
                event.listen(engine.sync_engine, "before_cursor_execute", capture)
                event.listen(engine.sync_engine, "before_execute", capture_selection)
                started = perf_counter_ns()
                try:
                    async with asyncio.timeout(profile.expiry_deadline_seconds):
                        assert (
                            await transaction.ci_economics_collection.expire_evidence(
                                limit=profile.cleanup_batch_size
                            )
                            == 100
                        )
                        await transaction.commit()
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture)
                    event.remove(engine.sync_engine, "before_execute", capture_selection)
                elapsed = perf_counter_ns() - started
            assert len(statements) == 301 and len(selection) == 1
            assert selection[0] == selected_query
            async with engine.connect() as connection:
                for relation in (
                    ci_workflow_attempt_snapshots,
                    ci_workflow_attempt_snapshot_jobs,
                    ci_job_measurement_reports,
                    ci_economics_budget_signals,
                ):
                    assert tuple(await connection.scalars(select(relation.c.subject_id))) == (
                        live_source.source_id,
                    )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(ci_workflow_attempt_collections)
                        .where(ci_workflow_attempt_collections.c.status == "expired")
                    )
                    == 100
                )
            assert await collection.expire_evidence(limit=100) == 0
            assert await collection.purge_tombstones(limit=100) == 0
            assert isinstance(engine.pool, AsyncAdaptedQueuePool) and engine.pool.checkedout() == 0
            with capsys.disabled():
                print(
                    json.dumps(
                        {
                            "observation": "ci-observation-cleanup-cost/v1",
                            "expiredAttempts": 100,
                            "expiredJobs": 200_000,
                            "expiredReports": 100,
                            "expiredSignals": 100,
                            "preparationNanoseconds": preparation_elapsed,
                            "committedSeedNanoseconds": committed_seed_elapsed,
                            "expansionNanoseconds": expansion_elapsed,
                            "expansionConstructionNanoseconds": expansion_construction_elapsed,
                            "expansionTransactionNanoseconds": expansion_transaction_elapsed,
                            "retentionShiftNanoseconds": retention_shift_elapsed,
                            "cleanupStatements": len(statements),
                            "committedCleanupNanoseconds": elapsed,
                            "populatedSelectionPlan": plan[0],
                            "nonClaims": [
                                "Hosted fixture evidence is not production capacity or CPU savings."
                            ],
                        },
                        sort_keys=True,
                    )
                )
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["snapshot_subject", "report_attempt"])
def test_cleanup_seed_admits_once_and_rolls_back_partial_construction(
    runtime_postgres_database_url: str, failure: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        collection = store(engine)
        budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
        statements: list[str] = []

        def capture(
            _connection: object, _cursor: object, statement: str, *_arguments: object
        ) -> None:
            statements.append(statement)

        try:
            now = await database_now(engine)
            prototype = snapshot(subject(1000), now)
            source = ProviderRunCollectionSource(prototype.attempt, now, "2026-03-10", "a" * 64)
            evidence = replace(prototype, subject_id=source.source_id)
            report = replace(measurement_report(attempt=source.attempt), reported_at=now)
            configured = await budgets.configure_policy(budget_command(report))
            assert isinstance(configured, BudgetPolicyCommitted)
            rejected_evidence = (
                replace(evidence, subject_id="e" * 64)
                if failure == "snapshot_subject"
                else evidence
            )
            rejected_report = (
                replace(report, attempt=replace(source.attempt, workflow_run_id=1001))
                if failure == "report_attempt"
                else report
            )
            expected_error = (
                PersistenceInvariantViolation if failure == "snapshot_subject" else ValueError
            )
            expected_message = (
                "CI economics snapshot completion failed"
                if failure == "snapshot_subject"
                else "report write crosses its source attempt"
            )
            event.listen(engine.sync_engine, "after_cursor_execute", capture)
            try:
                with pytest.raises(expected_error, match=expected_message):
                    await seed_cleanup_attempt(engine, source, rejected_evidence, rejected_report)
            finally:
                event.remove(engine.sync_engine, "after_cursor_execute", capture)
            assert sum("pg_catalog.pg_advisory_xact_lock_shared(" in sql for sql in statements) == 1
            assert any(
                sql.startswith("INSERT INTO ci_coordinator.ci_workflow_attempt_collections ")
                for sql in statements
            )
            assert any(
                sql.startswith("UPDATE ci_coordinator.ci_workflow_attempt_collections ")
                for sql in statements
            )
            if failure == "report_attempt":
                for relation in (ci_workflow_attempt_snapshots, ci_workflow_attempt_snapshot_jobs):
                    assert any(
                        sql.startswith(f"INSERT INTO ci_coordinator.{relation.name} ")
                        for sql in statements
                    )
            assert isinstance(engine.pool, AsyncAdaptedQueuePool) and engine.pool.checkedout() == 0
            async with engine.connect() as connection:
                for relation in (
                    ci_workflow_attempt_collections,
                    ci_workflow_attempt_snapshots,
                    ci_workflow_attempt_snapshot_jobs,
                    ci_job_measurement_reports,
                    ci_economics_budget_signals,
                ):
                    assert await connection.scalar(select(func.count()).select_from(relation)) == 0
            statements.clear()
            event.listen(engine.sync_engine, "after_cursor_execute", capture)
            try:
                await seed_cleanup_attempt(engine, source, evidence, report)
            finally:
                event.remove(engine.sync_engine, "after_cursor_execute", capture)
            assert sum("pg_catalog.pg_advisory_xact_lock_shared(" in sql for sql in statements) == 1
            assert isinstance(engine.pool, AsyncAdaptedQueuePool) and engine.pool.checkedout() == 0
            decoded = await collection.load_measurements(source.attempt)
            assert decoded is not None and isinstance(decoded.snapshot, IndependentAttemptSnapshot)
            assert decoded.snapshot.source == source and decoded.snapshot.evidence == evidence
            stored_report = await collection.load_measurement_report(
                source.attempt.scope, report.report_id
            )
            assert stored_report is not None
            assert stored_report.report == report and stored_report.source == source
            assert stored_report.origin == MeasurementReportOrigin("c" * 64, "d" * 64)
            signals = await budgets.list_signals(source.attempt.scope, after_cursor=None, limit=100)
            assert len(signals.items) == 1 and signals.next_cursor is None
            signal = signals.items[0]
            assert signal.policy == configured.policy
            assert signal.source_id == source.source_id and signal.report_id == report.report_id
            assert signal.report_digest == report.report_digest
            assert signal.measurement.counter == "cpu_user" and signal.measurement.value_us == 1000
            assert signal.command_exit_code == report.command_exit_code == 0
            assert signal.received_at == stored_report.received_at
            assert (
                signal.retain_until == stored_report.retain_until == decoded.snapshot.retain_until
            )
            async with engine.connect() as connection:
                row = (
                    (await connection.execute(select(ci_workflow_attempt_collections)))
                    .mappings()
                    .one()
                )
                assert decode_collection_source(dict(row)) == source
                assert (
                    row["status"] == "captured"
                    and row["evidence_retain_until"] == signal.retain_until
                )
                assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
            assert isinstance(engine.pool, AsyncAdaptedQueuePool) and engine.pool.checkedout() == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@asynccontextmanager
async def _expect_late_copy_fault(
    injected_error: RuntimeError | asyncio.CancelledError,
) -> AsyncIterator[Callable[[], None]]:
    completed_copy_rows = 0

    def after_copy_row() -> None:
        nonlocal completed_copy_rows
        completed_copy_rows += 1
        if completed_copy_rows == 402:
            raise injected_error

    with pytest.raises(type(injected_error)) as raised:
        async with asyncio.timeout(30):
            yield after_copy_row
    assert raised.value is injected_error, "COPY did not raise the exact injected exception"
    assert completed_copy_rows == 402, "COPY did not reach the 402-write checkpoint"


@pytest.mark.parametrize("failure", ["error", "cancelled"])
def test_expansion_copy_oracle_rejects_early_exceptions(
    failure: Literal["error", "cancelled"],
) -> None:
    async def scenario() -> None:
        error_type = asyncio.CancelledError if failure == "cancelled" else RuntimeError
        injected_error = error_type("fixture COPY interrupted")
        with pytest.raises(AssertionError, match="COPY did not raise the exact injected exception"):
            async with _expect_late_copy_fault(injected_error):
                raise error_type("fixture COPY interrupted")
        with pytest.raises(AssertionError, match="COPY did not reach the 402-write checkpoint"):
            async with _expect_late_copy_fault(injected_error):
                raise injected_error

    asyncio.run(scenario())


def test_expansion_copy_oracle_rejects_deadline_as_injected_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_timeout = asyncio.timeout

    def immediate_timeout(_delay: float | None) -> asyncio.Timeout:
        return original_timeout(0)

    async def scenario() -> None:
        injected_error = asyncio.CancelledError("fixture COPY interrupted")
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "timeout", immediate_timeout)
            with pytest.raises(TimeoutError) as raised:
                async with _expect_late_copy_fault(injected_error):
                    await asyncio.sleep(0)
        assert isinstance(raised.value.__cause__, asyncio.CancelledError)
        assert raised.value.__cause__ is not injected_error

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "cancelled"])
def test_expansion_copy_rolls_back_and_allows_exact_retry(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
    failure: Literal["error", "cancelled"],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(engine)
            prototype = snapshot(subject(1000), now)
            source = ProviderRunCollectionSource(prototype.attempt, now, "2026-03-10", "a" * 64)
            evidence = replace(prototype, subject_id=source.source_id)
            report = replace(measurement_report(attempt=source.attempt), reported_at=now)
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            assert isinstance(
                await budgets.configure_policy(budget_command(report)), BudgetPolicyCommitted
            )
            await seed_cleanup_attempt(engine, source, evidence, report)
            error_type = asyncio.CancelledError if failure == "cancelled" else RuntimeError
            injected_error = error_type("fixture COPY interrupted")
            async with _expect_late_copy_fault(injected_error) as after_copy_row:
                await expand_cleanup_snapshot(
                    admin,
                    evidence,
                    404,
                    after_copy_row=after_copy_row,
                )
            assert isinstance(admin.pool, AsyncAdaptedQueuePool)
            assert admin.pool.checkedout() == 0
            async with admin.connect() as connection:
                result = await connection.execute(
                    select(ci_workflow_attempt_snapshots).where(
                        ci_workflow_attempt_snapshots.c.subject_id == source.source_id
                    )
                )
                header = result.mappings().one()
                assert header["job_count"] == 1
                assert header["snapshot_digest"] == evidence.snapshot_digest
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(ci_workflow_attempt_snapshot_jobs)
                    )
                    == 1
                )
                assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
            async with asyncio.timeout(30):
                digest, _, _ = await expand_cleanup_snapshot(admin, evidence, 404)
                decoded = await store(engine).load_measurements(source.attempt)
            assert decoded is not None
            assert isinstance(decoded.snapshot, IndependentAttemptSnapshot)
            assert decoded.snapshot.evidence.snapshot_digest == digest
            assert len(decoded.snapshot.evidence.jobs) == 404
            assert admin.pool.checkedout() == 0
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())
