from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.ci_economics.budget_policy import (
    BudgetPolicyConfiguration,
    BudgetPolicySnapshot,
)
from ci_coordinator.ci_economics.reports import require_report_sample_key
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

BUDGET_POLICY_EVENT_TYPE: Final = "ci-economics-budget-configured/v1"


@dataclass(frozen=True, slots=True)
class ConfigureBudgetPolicy:
    scope: RepositoryScope
    policy_key: str
    expected_revision: int
    configuration: BudgetPolicyConfiguration
    operation_id: str
    actor: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("budget command requires exact repository scope")
        require_report_sample_key(self.policy_key)
        require_report_sample_key(self.operation_id)
        if (
            type(self.expected_revision) is not int
            or not 0 <= self.expected_revision < MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("expected budget revision must permit one safe increment")
        if type(self.configuration) is not BudgetPolicyConfiguration:
            raise TypeError("budget command requires exact configuration")
        if (
            type(self.actor) is not str
            or not self.actor
            or len(self.actor) > 512
            or "\x00" in self.actor
            or any(0xD800 <= ord(value) <= 0xDFFF for value in self.actor)
        ):
            raise ValueError("budget actor must be bounded Unicode scalar text")

    @property
    def next_policy(self) -> BudgetPolicySnapshot:
        return BudgetPolicySnapshot(
            self.scope, self.policy_key, self.expected_revision + 1, self.configuration
        )

    @property
    def command_digest(self) -> str:
        return hash_object(self.canonical_mapping())

    @property
    def audit_key(self) -> str:
        return (
            f"ci-economics-budget:{self.scope.installation_id}:"
            f"{self.scope.repository_id}:{self.operation_id}"
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-budget-command/v1",
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "policyKey": self.policy_key,
            "expectedRevision": self.expected_revision,
            "configuration": self.configuration.canonical_mapping(),
            "operationId": self.operation_id,
            "actor": self.actor,
        }


@dataclass(frozen=True, slots=True)
class BudgetPolicyCommitted:
    policy: BudgetPolicySnapshot
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.policy) is not BudgetPolicySnapshot or type(self.replayed) is not bool:
            raise TypeError("budget receipt requires exact policy and replay state")


@dataclass(frozen=True, slots=True)
class BudgetPolicyConflict:
    reason: Literal["revision_conflict", "operation_conflict", "capacity_reached"]

    def __post_init__(self) -> None:
        if self.reason not in {"revision_conflict", "operation_conflict", "capacity_reached"}:
            raise ValueError("unknown budget policy conflict")


type BudgetPolicyWriteResult = BudgetPolicyCommitted | BudgetPolicyConflict
