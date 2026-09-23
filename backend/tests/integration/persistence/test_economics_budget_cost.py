import asyncio
import json
from dataclasses import replace
from time import perf_counter_ns

import pytest
from ci_economics.budget_factories import budget_command
from ci_economics.report_factories import measurement_report
from sqlalchemy import Select, event, text
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store

from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_budget_adapters import TransactionalBudgetPolicyStore
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_economics_budget_signals

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("policy_count", [0, 16])
def test_budget_storage_and_actual_read_plan_cost(
    runtime_postgres_database_url: str,
    capsys: pytest.CaptureFixture[str],
    policy_count: int,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            command = budget_command(report)
            collection = store(engine)
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            assert await collection.register_provider_source(source) == "registered"
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                for index in range(policy_count):
                    configured = replace(
                        command, policy_key=f"budget-{index:02}", operation_id=f"create-{index}"
                    )
                    assert await transaction.budget_policies.configure_policy(
                        configured
                    ) == BudgetPolicyCommitted(configured.next_policy, False)
                await transaction.commit()

            durations = []
            for index in range(4):
                sample = replace(report, provider_job_id=404 + index, check_run_id=504 + index)
                started = perf_counter_ns()
                result = await collection.record_measurement_report(
                    source, sample, MeasurementReportOrigin("c" * 64, "d" * 64)
                )
                durations.append(perf_counter_ns() - started)
                assert result == "recorded"

            queries: list[str] = []

            def capture(_connection: object, statement: object, *_arguments: object) -> None:
                if isinstance(statement, Select) and statement.selected_columns.contains_column(
                    ci_economics_budget_signals.c.signal_id
                ):
                    queries.append(
                        str(
                            statement.compile(
                                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
                            )
                        )
                    )

            event.listen(engine.sync_engine, "before_execute", capture)
            try:
                page = await budgets.list_signals(command.scope, after_cursor=None, limit=20)
                assert len(page.items) == min(4 * policy_count, 20)
                filtered = await budgets.list_signals(
                    command.scope, after_cursor=None, limit=20, policy_key="budget-00", revision=1
                )
                assert len(filtered.items) == (4 if policy_count else 0)
                following = await budgets.list_signals(
                    command.scope,
                    after_cursor=page.items[0].signal_id if page.items else "0" * 64,
                    limit=20,
                )
                assert len(following.items) == min(max(4 * policy_count - 1, 0), 20)
            finally:
                event.remove(engine.sync_engine, "before_execute", capture)
            assert len(queries) == 3

            plans = []
            async with engine.connect() as connection:
                row_count, row_bytes = (
                    await connection.execute(
                        text(
                            "SELECT count(*), coalesce(sum(pg_column_size(s)), 0) "
                            "FROM ci_coordinator.ci_economics_budget_signals AS s"
                        )
                    )
                ).one()
                assert row_count == 4 * policy_count
                assert (row_bytes > 0) == (policy_count > 0)
                expected_rows = (
                    min(4 * policy_count, 21),
                    4 if policy_count else 0,
                    min(max(4 * policy_count - 1, 0), 21),
                )
                for query, expected in zip(queries, expected_rows, strict=True):
                    plan = await connection.scalar(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query)
                    )
                    assert isinstance(plan, list) and len(plan) == 1
                    assert plan[0]["Plan"]["Node Type"] == "Limit"
                    assert plan[0]["Plan"]["Actual Rows"] == expected
                    plans.append(plan[0])
            with capsys.disabled():
                print(
                    json.dumps(
                        {
                            "observation": "economics-budget-fixture-cost/v1",
                            "policyCount": policy_count,
                            "reportCount": 4,
                            "committedIngressNanoseconds": durations,
                            "signalRows": row_count,
                            "signalRowBytes": int(row_bytes),
                            "readPlans": plans,
                            "nonClaims": [
                                "Production capacity, CPU savings and causal speedup are unproven."
                            ],
                        },
                        sort_keys=True,
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
