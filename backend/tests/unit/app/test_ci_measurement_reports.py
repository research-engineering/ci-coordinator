from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import cast

import pytest
from ci_economics.factories import ATTEMPT
from ci_economics.report_factories import measurement_report, stored_report

from ci_coordinator.app.ci_economics import (
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.app.ci_measurement_reports import MeasurementReportReadService
from ci_coordinator.ci_economics.budget import EvaluatedReportBudget, ReportBudget
from ci_coordinator.ci_economics.catalog import MeasurementReportPage
from ci_coordinator.ci_economics.comparison import RetainedReportPair
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.ci_economics.reports import StoredMeasurementReport
from ci_coordinator.config_control import RepositoryScope


@dataclass
class _Authorizer:
    calls: list[object]
    allowed: bool = True

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append(("authorize", actor, scope))
        return self.allowed


@dataclass
class _Query:
    records: list[StoredMeasurementReport | BaseException | None]
    calls: list[object] = field(default_factory=list)

    async def list_measurement_reports(
        self, scope: RepositoryScope, source_id: str, *, after_cursor: str | None, limit: int
    ) -> MeasurementReportPage | None:
        raise AssertionError("unexpected report catalog read")

    async def load_measurement_report(
        self, scope: RepositoryScope, report_id: str
    ) -> StoredMeasurementReport | None:
        self.calls.append(("read", scope, report_id))
        result = self.records.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.mark.parametrize("allowed", [False, True])
def test_budget_uses_the_same_scope_first_retained_read(allowed: bool) -> None:
    record = stored_report()
    calls: list[object] = []
    service = MeasurementReportReadService(
        authorizer=_Authorizer(calls, allowed), query=_Query([record], calls)
    )
    result = asyncio.run(
        service.evaluate_budget(
            actor="actor",
            scope=ATTEMPT.scope,
            report_id=record.report.report_id,
            budget=ReportBudget("cpu_user", 999),
        )
    )
    if allowed:
        assert isinstance(result, EvaluatedReportBudget) and result.outcome == "breached"
        assert calls == [
            ("authorize", "actor", ATTEMPT.scope),
            ("read", ATTEMPT.scope, record.report.report_id),
        ]
    else:
        assert isinstance(result, CiEconomicsReadForbidden)
        assert calls == [("authorize", "actor", ATTEMPT.scope)]


@pytest.mark.parametrize("pair", (False, True))
def test_report_reads_authorize_before_any_query(pair: bool) -> None:
    calls: list[object] = []
    service = MeasurementReportReadService(
        authorizer=_Authorizer(calls, False), query=_Query([], calls)
    )
    result = asyncio.run(
        service.compare_reports(
            actor="actor", scope=ATTEMPT.scope, baseline_id="a" * 64, treatment_id="b" * 64
        )
        if pair
        else service.load_report(actor="actor", scope=ATTEMPT.scope, report_id="a" * 64)
    )
    assert isinstance(result, CiEconomicsReadForbidden)
    assert calls == [("authorize", "actor", ATTEMPT.scope)]


@pytest.mark.parametrize("same_id", (False, True))
def test_pair_preserves_complete_records_with_at_most_two_reads(same_id: bool) -> None:
    baseline = stored_report()
    treatment = (
        baseline
        if same_id
        else stored_report(measurement_report(attempt=replace(ATTEMPT, run_attempt=3)))
    )
    calls: list[object] = []
    query = _Query([baseline] if same_id else [baseline, treatment], calls)
    service = MeasurementReportReadService(authorizer=_Authorizer(calls), query=query)
    result = asyncio.run(
        service.compare_reports(
            actor="actor",
            scope=ATTEMPT.scope,
            baseline_id=baseline.report.report_id,
            treatment_id=treatment.report.report_id,
        )
    )
    assert result == RetainedReportPair(baseline, treatment)
    assert result.comparison.mismatches == (("same_attempt",) if same_id else ())
    assert calls == [
        ("authorize", "actor", ATTEMPT.scope),
        ("read", ATTEMPT.scope, baseline.report.report_id),
        *([] if same_id else [("read", ATTEMPT.scope, treatment.report.report_id)]),
    ]
    assert query.records == []


@pytest.mark.parametrize("second", (False, True))
@pytest.mark.parametrize("missing", (False, True))
def test_missing_or_unavailable_pair_operand_never_becomes_zero(
    second: bool, missing: bool
) -> None:
    baseline = stored_report()
    records: list[StoredMeasurementReport | BaseException | None] = [baseline] if second else []
    records.append(None if missing else CiEconomicsStoreUnavailable("offline"))
    query = _Query(records)
    service = MeasurementReportReadService(authorizer=_Authorizer([]), query=query)
    result = asyncio.run(
        service.compare_reports(
            actor="actor",
            scope=ATTEMPT.scope,
            baseline_id=baseline.report.report_id,
            treatment_id="b" * 64,
        )
    )
    assert isinstance(result, CiEconomicsReadNotFound if missing else CiEconomicsReadUnavailable)
    assert len(query.calls) == (2 if second else 1)


@pytest.mark.parametrize(
    "operand", ("type", "installation", "repository", "run", "attempt", "head", "sample")
)
def test_report_read_rebinds_every_source_and_slot_operand(operand: str) -> None:
    original = stored_report()
    report = original.report
    changes = {
        "installation": replace(ATTEMPT, scope=RepositoryScope(102, 202)),
        "repository": replace(ATTEMPT, scope=RepositoryScope(101, 203)),
        "run": replace(ATTEMPT, workflow_run_id=304),
        "attempt": replace(ATTEMPT, run_attempt=3),
        "head": replace(ATTEMPT, head_sha="c" * 40),
    }
    if operand == "type":
        changed = cast(StoredMeasurementReport, object())
    elif operand == "sample":
        changed = stored_report(replace(report, sample_key="another-sample"))
    else:
        changed = stored_report(replace(report, attempt=changes[operand]))
    service = MeasurementReportReadService(authorizer=_Authorizer([]), query=_Query([changed]))
    assert isinstance(
        asyncio.run(
            service.load_report(actor="actor", scope=ATTEMPT.scope, report_id=report.report_id)
        ),
        CiEconomicsReadUnavailable,
    )


@pytest.mark.parametrize("report_id", ("", "a" * 63, "A" * 64, "a" * 65, "../"))
def test_invalid_report_id_never_reaches_authorization_or_store(report_id: str) -> None:
    calls: list[object] = []
    service = MeasurementReportReadService(authorizer=_Authorizer(calls), query=_Query([], calls))
    with pytest.raises(ValueError):
        asyncio.run(service.load_report(actor="actor", scope=ATTEMPT.scope, report_id=report_id))
    assert calls == []


@pytest.mark.parametrize("error", (asyncio.CancelledError(), RuntimeError("programmer defect")))
def test_report_read_does_not_mask_cancellation_or_programmer_errors(error: BaseException) -> None:
    service = MeasurementReportReadService(authorizer=_Authorizer([]), query=_Query([error]))
    with pytest.raises(type(error)):
        asyncio.run(service.load_report(actor="actor", scope=ATTEMPT.scope, report_id="a" * 64))
