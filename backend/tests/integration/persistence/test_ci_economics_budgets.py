import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from ci_economics.budget_factories import budget_command
from ci_economics.report_factories import measurement_report
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from tests.integration.persistence._ci_economics_support import database_now, provider_source, store
from tests.integration.persistence._temporal_lock_support import wait_for_blocked_operation

from ci_coordinator.ci_economics.budget import ReportBudget
from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted, BudgetPolicyConflict
from ci_coordinator.ci_economics.budget_policy import BudgetPolicySnapshot
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin, StoredMeasurementReport
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork
from ci_coordinator.persistence.ci_economics_budget_adapters import TransactionalBudgetPolicyStore
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import CommitOutcomeUnknown
from ci_coordinator.persistence.schema import (
    audit_events,
    ci_economics_budget_policies,
    ci_economics_budget_signals,
    ci_job_measurement_reports,
)

pytestmark = pytest.mark.persistence
_ORIGIN = MeasurementReportOrigin("c" * 64, "d" * 64)


def test_policy_cas_audit_replay_quota_and_role_bounds(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            command = budget_command()
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            assert await budgets.list_policies(command.scope) == ()
            assert await budgets.configure_policy(command) == BudgetPolicyCommitted(
                command.next_policy, False
            )
            updated = replace(
                command,
                expected_revision=1,
                operation_id="disable",
                configuration=replace(command.configuration, enabled=False),
            )
            assert await budgets.configure_policy(updated) == BudgetPolicyCommitted(
                updated.next_policy, False
            )
            assert await budgets.configure_policy(command) == BudgetPolicyCommitted(
                command.next_policy, True
            )
            assert await budgets.list_policies(command.scope) == (updated.next_policy,)
            assert await budgets.configure_policy(
                replace(command, operation_id="stale")
            ) == BudgetPolicyConflict("revision_conflict")
            assert await budgets.configure_policy(
                replace(command, configuration=updated.configuration)
            ) == BudgetPolicyConflict("operation_conflict")
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                policy_keys = ("Z", "a-", "a.", "a_", "A", *(f"policy-{i:02}" for i in range(10)))
                for index in range(1, 16):
                    item = replace(
                        command, policy_key=policy_keys[index - 1], operation_id=f"create-{index}"
                    )
                    assert await transaction.budget_policies.configure_policy(
                        item
                    ) == BudgetPolicyCommitted(item.next_policy, False)
                assert await transaction.budget_policies.configure_policy(
                    replace(command, policy_key="overflow", operation_id="overflow")
                ) == BudgetPolicyConflict("capacity_reached")
                await transaction.commit()
            assert tuple(
                policy.policy_key for policy in await budgets.list_policies(command.scope)
            ) == (*sorted((*policy_keys, command.policy_key)),)
            assert await budgets.list_policies(RepositoryScope(102, 202)) == ()
            async with engine.begin() as connection:
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 17
                for statement in (
                    delete(ci_economics_budget_policies),
                    update(ci_economics_budget_policies).values(policy_key="renamed"),
                    delete(ci_economics_budget_signals),
                    update(ci_economics_budget_signals).values(value_us=0),
                ):
                    with pytest.raises(DBAPIError):
                        async with connection.begin_nested():
                            await connection.execute(statement)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_every_matching_policy_is_atomic_with_report_and_replay_is_historical(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
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
                for index in range(16):
                    configured = replace(
                        command, policy_key=f"budget-{index:02}", operation_id=f"create-{index}"
                    )
                    assert isinstance(
                        await transaction.budget_policies.configure_policy(configured),
                        BudgetPolicyCommitted,
                    )
                await transaction.commit()
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                original = transaction.budget_signals.record_signals

                async def fail_after_signals(
                    policies: tuple[BudgetPolicySnapshot, ...], record: StoredMeasurementReport
                ) -> None:
                    await original(policies, record)
                    raise CiEconomicsStoreUnavailable("injected failure after signal insertion")

                with monkeypatch.context() as patch:
                    patch.setattr(
                        transaction.budget_signals,
                        "record_signals",
                        AsyncMock(side_effect=fail_after_signals),
                    )
                    with pytest.raises(CiEconomicsStoreUnavailable):
                        await transaction.ci_measurement_reports.record_measurement_report(
                            source, report, _ORIGIN
                        )
            async with engine.connect() as connection:
                for table in (ci_job_measurement_reports, ci_economics_budget_signals):
                    assert await connection.scalar(select(func.count()).select_from(table)) == 0
            assert await collection.record_measurement_report(source, report, _ORIGIN) == "recorded"
            signals = await budgets.list_signals(command.scope, after_cursor=None, limit=100)
            assert len(signals.items) == 16 and signals.next_cursor is None
            assert {signal.policy.revision for signal in signals.items} == {1}
            assert {signal.outcome for signal in signals.items} == {"within_budget"}
            changed = replace(
                command,
                policy_key="budget-00",
                expected_revision=1,
                operation_id="tighten",
                configuration=replace(command.configuration, budget=ReportBudget("cpu_user", 0)),
            )
            assert isinstance(await budgets.configure_policy(changed), BudgetPolicyCommitted)
            assert await collection.record_measurement_report(source, report, _ORIGIN) == "replayed"
            assert (
                await collection.record_measurement_report(
                    source, replace(report, command_exit_code=1), _ORIGIN
                )
                == "report_conflict"
            )
            assert (
                await budgets.list_signals(command.scope, after_cursor=None, limit=100) == signals
            )
            assert (
                await budgets.list_signals(
                    command.scope, after_cursor=None, limit=20, policy_key="budget-00", revision=2
                )
            ).items == ()
            first = await budgets.list_signals(command.scope, after_cursor=None, limit=1)
            assert first.next_cursor == first.items[0].signal_id
            rest = await budgets.list_signals(
                command.scope, after_cursor=first.next_cursor, limit=100
            )
            assert first.items + rest.items == signals.items
            assert (
                await budgets.list_signals(RepositoryScope(102, 202), after_cursor=None, limit=1)
            ).items == ()
            async with engine.connect() as connection:
                rows, maximum = (
                    await connection.execute(
                        select(
                            func.count(),
                            func.max(
                                func.octet_length(ci_economics_budget_signals.c.policy_canonical)
                            ),
                        )
                    )
                ).one()
                assert rows == 16 and maximum <= 2048
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("include_matching", [False, True])
def test_report_acceptance_skips_nonmatching_policies_without_losing_matching_signals(
    runtime_postgres_database_url: str, include_matching: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            command = budget_command(report)
            config = command.configuration
            configurations = {
                "disabled": replace(config, enabled=False),
                "sample": replace(config, selector=replace(config.selector, sample_key="other")),
                "producer": replace(
                    config, selector=replace(config.selector, producer_digest="f" * 64)
                ),
                "runner": replace(
                    config, selector=replace(config.selector, runner_class_digest="f" * 64)
                ),
            }
            if include_matching:
                configurations.update(
                    {
                        "exact": config,
                        "wildcard": replace(
                            config, selector=replace(config.selector, runner_class_digest=None)
                        ),
                    }
                )
            async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                for key, configuration in configurations.items():
                    assert isinstance(
                        await transaction.budget_policies.configure_policy(
                            replace(
                                command,
                                policy_key=key,
                                operation_id=key,
                                configuration=configuration,
                            )
                        ),
                        BudgetPolicyCommitted,
                    )
                await transaction.commit()
            collection = store(engine)
            assert await collection.register_provider_source(source) == "registered"
            assert await collection.record_measurement_report(source, report, _ORIGIN) == "recorded"
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            page = await budgets.list_signals(command.scope, after_cursor=None, limit=100)
            assert {item.policy.policy_key for item in page.items} == (
                {"exact", "wildcard"} if include_matching else set()
            )
            assert all(
                item.policy
                == replace(
                    command,
                    policy_key=item.policy.policy_key,
                    configuration=configurations[item.policy.policy_key],
                ).next_policy
                for item in page.items
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_structurally_valid_but_contradictory_signal_is_rejected_on_read(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            command = budget_command(report)
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            assert isinstance(await budgets.configure_policy(command), BudgetPolicyCommitted)
            assert await store(engine).register_provider_source(source) == "registered"
            assert (
                await store(engine).record_measurement_report(source, report, _ORIGIN) == "recorded"
            )
            page = await budgets.list_signals(command.scope, after_cursor=None, limit=20)
            assert len(page.items) == 1 and page.items[0].outcome == "within_budget"
            assert page.items[0].measurement.value_us == command.configuration.budget.maximum_us
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.ci_economics_budget_signals "
                        "DISABLE TRIGGER tr_ci_economics_budget_signals_retention_guard"
                    )
                )
                await connection.execute(
                    update(ci_economics_budget_signals).values(outcome="breached")
                )
                await connection.execute(
                    text(
                        "ALTER TABLE ci_coordinator.ci_economics_budget_signals "
                        "ENABLE TRIGGER tr_ci_economics_budget_signals_retention_guard"
                    )
                )
            with pytest.raises(CiEconomicsStoreUnavailable):
                await budgets.list_signals(command.scope, after_cursor=None, limit=20)
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("report_first", [False, True])
def test_policy_revision_linearizes_against_report_admission(
    runtime_postgres_database_url: str, postgres_database_url: str, report_first: bool
) -> None:
    async def scenario() -> None:
        engine, admin = (
            create_postgres_engine(runtime_postgres_database_url),
            create_postgres_engine(postgres_database_url),
        )
        try:
            source = provider_source(303, await database_now(engine))
            report = measurement_report(attempt=source.attempt)
            original = budget_command(report)
            changed = replace(
                original,
                expected_revision=1,
                operation_id="tighten",
                configuration=replace(original.configuration, budget=ReportBudget("cpu_user", 0)),
            )
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            assert isinstance(await budgets.configure_policy(original), BudgetPolicyCommitted)
            assert await store(engine).register_provider_source(source) == "registered"
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def waiting() -> None:
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    pid = await transaction.budget_policies._connection.scalar(
                        select(func.pg_backend_pid())
                    )
                    assert type(pid) is int
                    waiter_pid.set_result(pid)
                    if report_first:
                        assert isinstance(
                            await transaction.budget_policies.configure_policy(changed),
                            BudgetPolicyCommitted,
                        )
                    else:
                        assert (
                            await transaction.ci_measurement_reports.record_measurement_report(
                                source, report, _ORIGIN
                            )
                            == "recorded"
                        )
                    await transaction.commit()

            async with (
                asyncio.timeout(20),
                asyncio.TaskGroup() as group,
                PostgresCiEconomicsUnitOfWork(engine) as holder,
            ):
                if report_first:
                    assert (
                        await holder.ci_measurement_reports.record_measurement_report(
                            source, report, _ORIGIN
                        )
                        == "recorded"
                    )
                else:
                    assert isinstance(
                        await holder.budget_policies.configure_policy(changed),
                        BudgetPolicyCommitted,
                    )
                pid = await holder.budget_policies._connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                task = group.create_task(waiting())
                await wait_for_blocked_operation(admin, pid, task, waiter_pid=await waiter_pid)
                await holder.commit()
            page = await budgets.list_signals(original.scope, after_cursor=None, limit=20)
            assert len(page.items) == 1
            assert page.items[0].policy.revision == (1 if report_first else 2)
            assert page.items[0].outcome == ("within_budget" if report_first else "breached")
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_concurrent_policy_creates_have_one_winner_and_one_audit_event(
    runtime_postgres_database_url: str, postgres_database_url: str
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            command = budget_command()
            waiter_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def competing() -> None:
                async with PostgresCiEconomicsUnitOfWork(engine) as transaction:
                    pid = await transaction.budget_policies._connection.scalar(
                        select(func.pg_backend_pid())
                    )
                    assert type(pid) is int
                    waiter_pid.set_result(pid)
                    assert await transaction.budget_policies.configure_policy(
                        replace(command, operation_id="competitor")
                    ) == BudgetPolicyConflict("revision_conflict")

            async with (
                asyncio.timeout(20),
                asyncio.TaskGroup() as group,
                PostgresCiEconomicsUnitOfWork(engine) as holder,
            ):
                assert await holder.budget_policies.configure_policy(
                    command
                ) == BudgetPolicyCommitted(command.next_policy, False)
                pid = await holder.budget_policies._connection.scalar(select(func.pg_backend_pid()))
                assert type(pid) is int
                task = group.create_task(competing())
                await wait_for_blocked_operation(admin, pid, task, waiter_pid=await waiter_pid)
                await holder.commit()
            budgets = TransactionalBudgetPolicyStore(lambda: PostgresCiEconomicsUnitOfWork(engine))
            assert await budgets.list_policies(command.scope) == (command.next_policy,)
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 1
        finally:
            await engine.dispose()
            await admin.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["policy", "report"])
def test_lost_commit_acknowledgement_replays_original_budget_evidence(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch, operation: str
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
            if operation == "report":
                assert isinstance(await budgets.configure_policy(command), BudgetPolicyCommitted)
            original_commit = PostgresCiEconomicsUnitOfWork.commit

            async def commit_without_acknowledgement(
                transaction: PostgresCiEconomicsUnitOfWork,
            ) -> None:
                await original_commit(transaction)
                raise CommitOutcomeUnknown("injected lost acknowledgement after real commit")

            with monkeypatch.context() as patch:
                # noinspection PyUnresolvedReferences
                patch.setattr(
                    PostgresCiEconomicsUnitOfWork, "commit", commit_without_acknowledgement
                )
                with pytest.raises(CiEconomicsStoreUnavailable):
                    if operation == "policy":
                        await budgets.configure_policy(command)
                    else:
                        await collection.record_measurement_report(source, report, _ORIGIN)
            assert await budgets.list_policies(command.scope) == (command.next_policy,)
            if operation == "policy":
                assert await budgets.configure_policy(command) == BudgetPolicyCommitted(
                    command.next_policy, True
                )
            else:
                original = await budgets.list_signals(command.scope, after_cursor=None, limit=20)
                assert len(original.items) == 1
                assert (
                    await collection.record_measurement_report(source, report, _ORIGIN)
                    == "replayed"
                )
                assert (
                    await budgets.list_signals(command.scope, after_cursor=None, limit=20)
                    == original
                )
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(audit_events)) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
