from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.ci_economics.budget import EvaluatedReportBudget, ReportBudget
from ci_coordinator.ci_economics.reports import ReportCounter, ReportMeasurement

from .report_factories import stored_report


@pytest.mark.parametrize(
    "counter,value", [("cpu_user", 1000), ("cpu_system", 200), ("elapsed", 800)]
)
@pytest.mark.parametrize(
    "offset,outcome", [(-1, "breached"), (0, "within_budget"), (1, "within_budget")]
)
def test_exact_counter_and_inclusive_threshold(
    counter: ReportCounter, value: int, offset: int, outcome: str
) -> None:
    evaluation = EvaluatedReportBudget(stored_report(), ReportBudget(counter, value + offset))
    assert evaluation.measurement.counter == counter
    assert evaluation.measurement.value_us == value
    assert evaluation.outcome == outcome


def test_missing_counter_is_not_zero_or_within_budget() -> None:
    record = stored_report()
    report = replace(
        record.report,
        measurements=tuple(
            ReportMeasurement(item.counter, None, "counter_error")
            if item.counter == "cpu_user"
            else item
            for item in record.report.measurements
        ),
    )
    evaluation = EvaluatedReportBudget(replace(record, report=report), ReportBudget("cpu_user", 0))
    assert evaluation.outcome == "insufficient_evidence"
    assert evaluation.measurement.unavailable_reason == "counter_error"


@pytest.mark.parametrize("value", [-1, True, 1.0, "1", 9_007_199_254_740_992])
def test_threshold_admission_rejects_invalid_scalar_before_comparison(value: object) -> None:
    with pytest.raises(ValueError):
        ReportBudget("cpu_user", cast(int, value))


def test_unknown_counter_cannot_silently_choose_another_measurement() -> None:
    with pytest.raises(ValueError):
        ReportBudget(cast(ReportCounter, "total_cpu"), 1)
