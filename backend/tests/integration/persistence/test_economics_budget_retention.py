import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.budget_factories import budget_command
from ci_economics.report_factories import measurement_report
from sqlalchemy import func, select, text
from tests.integration.persistence._ci_economics_support import (
    database_now,
    snapshot,
    store,
    subject,
)
from tests.integration.persistence._economics_retention_support import shift_retention_epoch

from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_budget_adapters import TransactionalBudgetPolicyStore
from ci_coordinator.persistence.ci_economics_collection_codec import decode_collection_state
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_economics_budget_signals,
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
    ci_workflow_attempt_snapshot_jobs,
    ci_workflow_attempt_snapshots,
)

pytestmark = pytest.mark.persistence


def test_report_signals_follow_complete_source_retention_without_recollection(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            now = await database_now(engine)
            provider = snapshot(subject(990), now)
            source = ProviderRunCollectionSource(provider.attempt, now, "2026-03-10", "a" * 64)
            evidence = replace(provider, subject_id=source.source_id)
            report = measurement_report(attempt=source.attempt)
            policy = budget_command(report)
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            collection = store(engine)
            assert isinstance(await budgets.configure_policy(policy), BudgetPolicyCommitted)
            assert await collection.register_provider_source(source) == "registered"
            claim = await collection.claim_next(worker_id="1" * 64)
            assert claim is not None
            assert await collection.record_snapshot(claim, evidence) == "captured"
            assert (
                await collection.record_measurement_report(
                    source, report, MeasurementReportOrigin("c" * 64, "d" * 64)
                )
                == "recorded"
            )
            original = await budgets.list_signals(policy.scope, after_cursor=None, limit=20)
            assert len(original.items) == 1
            assert await collection.expire_evidence(limit=1) == 0
            assert await collection.purge_tombstones(limit=1) == 0

            await shift_retention_epoch(admin, source.source_id, timedelta(days=90, hours=1))
            async with admin.connect() as connection:
                row = (
                    (await connection.execute(select(ci_workflow_attempt_collections)))
                    .mappings()
                    .one()
                )
                state = decode_collection_state(dict(row))
                assert state.status == "captured"
                assert (
                    state.evidence_retain_until
                    < await database_now(engine)
                    < state.tombstone_retain_until
                )
                for table in (
                    ci_job_measurement_reports,
                    ci_economics_budget_signals,
                    ci_workflow_attempt_snapshots,
                ):
                    assert (
                        await connection.scalar(select(table.c.retain_until))
                        == state.evidence_retain_until
                    )
                assert await connection.scalar(text("SHOW session_replication_role")) == "origin"
            assert (
                await budgets.list_signals(policy.scope, after_cursor=None, limit=20)
            ).items == ()
            assert await collection.expire_evidence(limit=1) == 1
            assert await collection.expire_evidence(limit=1) == 0
            async with engine.connect() as connection:
                for table in (
                    ci_job_measurement_reports,
                    ci_economics_budget_signals,
                    ci_workflow_attempt_snapshots,
                    ci_workflow_attempt_snapshot_jobs,
                ):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
                assert (
                    await connection.scalar(select(ci_workflow_attempt_collections.c.status))
                    == "expired"
                )
            assert await collection.purge_tombstones(limit=1) == 0
            await shift_retention_epoch(admin, source.source_id, timedelta(days=1))
            assert await collection.purge_tombstones(limit=1) == 1
            assert await collection.purge_tombstones(limit=1) == 0
            old_source = replace(
                source, run_created_at=source.run_created_at - timedelta(days=91, hours=1)
            )
            assert old_source.source_id == source.source_id
            assert await collection.register_provider_source(old_source) == "outside_source_window"
            assert await collection.claim_next(worker_id="1" * 64) is None
            assert await budgets.list_policies(policy.scope) == (policy.next_policy,)
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())
