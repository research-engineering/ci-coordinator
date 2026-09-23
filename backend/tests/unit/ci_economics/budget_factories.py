from ci_coordinator.ci_economics.budget import ReportBudget
from ci_coordinator.ci_economics.budget_commands import ConfigureBudgetPolicy
from ci_coordinator.ci_economics.budget_policy import BudgetPolicyConfiguration, BudgetSelector
from ci_coordinator.ci_economics.reports import JobMeasurementReport

from .report_factories import measurement_report


def budget_command(report: JobMeasurementReport | None = None) -> ConfigureBudgetPolicy:
    report = measurement_report() if report is None else report
    return ConfigureBudgetPolicy(
        report.attempt.scope,
        "backend-cpu",
        0,
        BudgetPolicyConfiguration(
            True,
            BudgetSelector(
                report.sample_key,
                report.producer_digest,
                report.workload.runner_class_digest,
            ),
            ReportBudget("cpu_user", 1_000),
        ),
        "create-budget",
        "budget-operator",
    )
