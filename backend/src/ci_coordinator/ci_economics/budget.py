from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.ci_economics.reports import (
    REPORT_COUNTERS,
    ReportCounter,
    ReportMeasurement,
    StoredMeasurementReport,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type ReportBudgetOutcome = Literal["breached", "within_budget", "insufficient_evidence"]


@dataclass(frozen=True, slots=True)
class ReportBudget:
    counter: ReportCounter
    maximum_us: int

    def __post_init__(self) -> None:
        if type(self.counter) is not str or self.counter not in REPORT_COUNTERS:
            raise ValueError("budget requires an admitted report counter")
        if type(self.maximum_us) is not int or not 0 <= self.maximum_us <= MAX_SAFE_JSON_INTEGER:
            raise ValueError("budget requires a nonnegative safe microsecond threshold")


@dataclass(frozen=True, slots=True)
class EvaluatedReportBudget:
    record: StoredMeasurementReport
    budget: ReportBudget

    def __post_init__(self) -> None:
        if (
            type(self.record) is not StoredMeasurementReport
            or type(self.budget) is not ReportBudget
        ):
            raise TypeError("budget evaluation requires an exact retained report and threshold")

    @property
    def measurement(self) -> ReportMeasurement:
        return next(
            item for item in self.record.report.measurements if item.counter == self.budget.counter
        )

    @property
    def outcome(self) -> ReportBudgetOutcome:
        return report_budget_outcome(self.measurement, self.budget)


def report_budget_outcome(
    measurement: ReportMeasurement, budget: ReportBudget
) -> ReportBudgetOutcome:
    if type(measurement) is not ReportMeasurement or type(budget) is not ReportBudget:
        raise TypeError("budget outcome requires exact measurement and threshold")
    if measurement.counter != budget.counter:
        raise ValueError("budget outcome crosses its counter")
    value = measurement.value_us
    if value is None:
        return "insufficient_evidence"
    return "breached" if value > budget.maximum_us else "within_budget"
