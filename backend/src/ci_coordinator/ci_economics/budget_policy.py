from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ci_coordinator.ci_economics.budget import ReportBudget
from ci_coordinator.ci_economics.reports import (
    REPORT_METHOD,
    JobMeasurementReport,
    require_report_digest,
    require_report_sample_key,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

MAX_BUDGET_POLICIES_PER_REPOSITORY: Final = 16


@dataclass(frozen=True, slots=True)
class BudgetSelector:
    sample_key: str
    producer_digest: str
    runner_class_digest: str | None

    def __post_init__(self) -> None:
        require_report_sample_key(self.sample_key)
        require_report_digest(self.producer_digest)
        if self.runner_class_digest is not None:
            require_report_digest(self.runner_class_digest)

    def matches(self, report: JobMeasurementReport) -> bool:
        if type(report) is not JobMeasurementReport:
            raise TypeError("budget matching requires an exact measurement report")
        return (
            report.sample_key == self.sample_key
            and report.producer_digest == self.producer_digest
            and (
                self.runner_class_digest is None
                or report.workload.runner_class_digest == self.runner_class_digest
            )
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "sampleKey": self.sample_key,
            "producerDigest": self.producer_digest,
            "method": REPORT_METHOD,
            "runnerClassDigest": self.runner_class_digest,
        }


@dataclass(frozen=True, slots=True)
class BudgetPolicyConfiguration:
    enabled: bool
    selector: BudgetSelector
    budget: ReportBudget

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("budget enabled state must be an exact boolean")
        if type(self.selector) is not BudgetSelector or type(self.budget) is not ReportBudget:
            raise TypeError("budget configuration requires exact selector and threshold")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "selector": self.selector.canonical_mapping(),
            "counter": self.budget.counter,
            "maximumUs": self.budget.maximum_us,
        }


@dataclass(frozen=True, slots=True)
class BudgetPolicySnapshot:
    scope: RepositoryScope
    policy_key: str
    revision: int
    configuration: BudgetPolicyConfiguration

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("budget policy requires an exact repository scope")
        require_report_sample_key(
            self.policy_key, message="budget policy key must be a bounded ASCII identifier"
        )
        if type(self.revision) is not int or not 1 <= self.revision <= MAX_SAFE_JSON_INTEGER:
            raise ValueError("budget policy revision must be a positive safe integer")
        if type(self.configuration) is not BudgetPolicyConfiguration:
            raise TypeError("budget policy requires an exact configuration")

    def matches(self, report: JobMeasurementReport) -> bool:
        if type(report) is not JobMeasurementReport:
            raise TypeError("budget matching requires an exact measurement report")
        return (
            report.attempt.scope == self.scope
            and self.configuration.enabled
            and self.configuration.selector.matches(report)
        )

    @property
    def policy_digest(self) -> str:
        return hash_object(self.canonical_mapping())

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-budget-policy/v1",
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "policyKey": self.policy_key,
            "revision": self.revision,
            "configuration": self.configuration.canonical_mapping(),
        }
