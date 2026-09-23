from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import cast

from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.budget import ReportBudgetOutcome
from ci_coordinator.ci_economics.budget_policy import (
    MAX_BUDGET_POLICIES_PER_REPOSITORY,
    BudgetPolicySnapshot,
)
from ci_coordinator.ci_economics.budget_signal import BudgetSignal, BudgetSignalPage
from ci_coordinator.ci_economics.catalog import require_catalog_page
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.ci_economics.reports import (
    CounterUnavailableReason,
    ReportCounter,
    ReportMeasurement,
    StoredMeasurementReport,
    require_report_digest,
    require_report_sample_key,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.persistence.ci_economics_budget_codec import (
    decode_budget_policy,
    encode_budget_policy,
)
from ci_coordinator.persistence.schema import (
    ci_economics_budget_signals,
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
)


class _PostgresBudgetSignalRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def record_signals(
        self, policies: tuple[BudgetPolicySnapshot, ...], record: StoredMeasurementReport
    ) -> None:
        self._ensure_active()
        if type(policies) is not tuple or len(policies) > MAX_BUDGET_POLICIES_PER_REPOSITORY:
            raise ValueError("budget evaluation requires a bounded policy set")
        try:
            signals = tuple(
                BudgetSignal.evaluate(policy, record)
                for policy in policies
                if policy.matches(record.report)
            )
            if len({signal.policy.policy_key for signal in signals}) != len(signals):
                raise ValueError("budget evaluation repeats a policy identity")
            if signals:
                await self._connection.execute(
                    insert(ci_economics_budget_signals), [_to_row(signal) for signal in signals]
                )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            self._mark_rollback_required()
            raise CiEconomicsStoreUnavailable("budget signals could not be retained") from error

    async def list_signals(
        self,
        scope: RepositoryScope,
        *,
        after_cursor: str | None,
        limit: int,
        policy_key: str | None = None,
        revision: int | None = None,
        outcome: ReportBudgetOutcome | None = None,
    ) -> BudgetSignalPage:
        self._ensure_active()
        require_catalog_page(scope, limit)
        if after_cursor is not None:
            require_report_digest(after_cursor)
        if policy_key is not None:
            require_report_sample_key(policy_key)
        if revision is not None and (
            policy_key is None
            or type(revision) is not int
            or not 1 <= revision <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("signal revision requires a policy key and safe positive revision")
        if outcome is not None and outcome not in {
            "breached",
            "within_budget",
            "insufficient_evidence",
        }:
            raise ValueError("unknown signal outcome")
        table, report, source = (
            ci_economics_budget_signals,
            ci_job_measurement_reports,
            ci_workflow_attempt_collections,
        )
        cursor_key = table.c.signal_id.collate("C")
        statement = (
            select(table)
            .join(report, table.c.report_id == report.c.report_id)
            .join(source, table.c.subject_id == source.c.subject_id)
            .where(
                table.c.installation_id == scope.installation_id,
                table.c.repository_id == scope.repository_id,
                source.c.installation_id == scope.installation_id,
                source.c.repository_id == scope.repository_id,
                source.c.source_kind == "provider_run",
                source.c.status != "expired",
                table.c.retain_until == source.c.evidence_retain_until,
                table.c.retain_until == report.c.retain_until,
                table.c.report_digest == report.c.report_digest,
                table.c.received_at == report.c.received_at,
                table.c.retain_until > func.statement_timestamp(),
            )
        )
        if after_cursor is not None:
            statement = statement.where(cursor_key > after_cursor)
        if policy_key is not None:
            statement = statement.where(table.c.policy_key == policy_key)
        if revision is not None:
            statement = statement.where(table.c.policy_revision == revision)
        if outcome is not None:
            statement = statement.where(table.c.outcome == outcome)
        try:
            rows = (
                (await self._connection.execute(statement.order_by(cursor_key).limit(limit + 1)))
                .mappings()
                .all()
            )
            items = tuple(_from_row(row) for row in rows[:limit])
            return BudgetSignalPage(items, items[-1].signal_id if len(rows) > limit else None)
        except asyncio.CancelledError:
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("budget signals unavailable") from error


def _to_row(signal: BudgetSignal) -> dict[str, object]:
    policy = signal.policy
    return dict(
        signal_id=signal.signal_id,
        installation_id=policy.scope.installation_id,
        repository_id=policy.scope.repository_id,
        policy_key=policy.policy_key,
        policy_revision=policy.revision,
        policy_canonical=encode_budget_policy(policy),
        report_id=signal.report_id,
        subject_id=signal.source_id,
        report_digest=signal.report_digest,
        counter=signal.measurement.counter,
        value_us=signal.measurement.value_us,
        unavailable_reason=signal.measurement.unavailable_reason,
        command_exit_code=signal.command_exit_code,
        outcome=signal.outcome,
        received_at=signal.received_at,
        retain_until=signal.retain_until,
    )


def _from_row(row: RowMapping) -> BudgetSignal:
    signal = BudgetSignal(
        decode_budget_policy(row["policy_canonical"]),
        cast(str, row["subject_id"]),
        cast(str, row["report_id"]),
        cast(str, row["report_digest"]),
        ReportMeasurement(
            cast(ReportCounter, row["counter"]),
            cast(int | None, row["value_us"]),
            cast(CounterUnavailableReason | None, row["unavailable_reason"]),
        ),
        cast(int, row["command_exit_code"]),
        cast(datetime, row["received_at"]),
        cast(datetime, row["retain_until"]),
    )
    expected = _to_row(signal)
    for name, value in expected.items():
        if name != "policy_canonical" and row[name] != value:
            raise ValueError("budget signal row contradicts canonical identity or outcome")
    return signal
