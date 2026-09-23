from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ci_coordinator.ci_economics.budget import (
    EvaluatedReportBudget,
    ReportBudgetOutcome,
    report_budget_outcome,
)
from ci_coordinator.ci_economics.budget_policy import BudgetPolicySnapshot
from ci_coordinator.ci_economics.reports import (
    ReportMeasurement,
    StoredMeasurementReport,
    require_report_digest,
)
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


@dataclass(frozen=True, slots=True)
class BudgetSignal:
    policy: BudgetPolicySnapshot
    source_id: str
    report_id: str
    report_digest: str
    measurement: ReportMeasurement
    command_exit_code: int
    received_at: datetime
    retain_until: datetime

    def __post_init__(self) -> None:
        if type(self.policy) is not BudgetPolicySnapshot or not self.policy.configuration.enabled:
            raise ValueError("signal requires an exact enabled policy snapshot")
        for value in (self.source_id, self.report_id, self.report_digest):
            require_report_digest(value)
        report_budget_outcome(self.measurement, self.policy.configuration.budget)
        if (
            type(self.command_exit_code) is not int
            or abs(self.command_exit_code) > MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("signal command outcome must be a safe integer")
        for name in ("received_at", "retain_until"):
            value = getattr(self, name)
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("signal requires aware receipt and retention")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.received_at >= self.retain_until:
            raise ValueError("signal receipt must precede expiry")

    @classmethod
    def evaluate(
        cls, policy: BudgetPolicySnapshot, record: StoredMeasurementReport
    ) -> BudgetSignal:
        if type(record) is not StoredMeasurementReport or type(policy) is not BudgetPolicySnapshot:
            raise TypeError("signal evaluation requires exact policy and retained report")
        if not policy.matches(record.report):
            raise ValueError("report does not match budget policy")
        return cls(
            policy,
            record.source.source_id,
            record.report.report_id,
            record.report.report_digest,
            EvaluatedReportBudget(record, policy.configuration.budget).measurement,
            record.report.command_exit_code,
            record.received_at,
            record.retain_until,
        )

    @property
    def signal_id(self) -> str:
        return hash_object(
            {
                "schemaVersion": "ci-economics-budget-signal-identity/v1",
                "policyDigest": self.policy.policy_digest,
                "reportId": self.report_id,
            }
        )

    @property
    def outcome(self) -> ReportBudgetOutcome:
        return report_budget_outcome(self.measurement, self.policy.configuration.budget)

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-budget-signal/v1",
            "signalId": self.signal_id,
            "policy": self.policy.canonical_mapping(),
            "policyDigest": self.policy.policy_digest,
            "sourceId": self.source_id,
            "reportId": self.report_id,
            "reportDigest": self.report_digest,
            "measurement": self.measurement.canonical_mapping(),
            "commandExitCode": self.command_exit_code,
            "receivedAt": self.received_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "retainUntil": self.retain_until.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class BudgetSignalPage:
    items: tuple[BudgetSignal, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if (
            type(self.items) is not tuple
            or len(self.items) > 100
            or any(type(item) is not BudgetSignal for item in self.items)
        ):
            raise ValueError("signal page must be a bounded exact tuple")
        identifiers = tuple(item.signal_id for item in self.items)
        if tuple(sorted(set(identifiers))) != identifiers:
            raise ValueError("signal page must be unique and ordered")
        if self.next_cursor is not None and (
            not identifiers or self.next_cursor != identifiers[-1]
        ):
            raise ValueError("signal cursor must equal the last returned identity")
