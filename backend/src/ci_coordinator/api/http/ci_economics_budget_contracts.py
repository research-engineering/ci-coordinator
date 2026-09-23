from datetime import datetime
from typing import Literal

from pydantic import Field

from ci_coordinator.api.http.ci_economics_source_contracts import EconomicsSourceScopeBody
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.ci_economics.budget import ReportBudgetOutcome
from ci_coordinator.ci_economics.budget_commands import ConfigureBudgetPolicy
from ci_coordinator.ci_economics.budget_payload import (
    BudgetConfigurationPayload,
    BudgetDigest,
    BudgetKey,
    BudgetPolicyPayload,
    BudgetPositiveId,
)
from ci_coordinator.ci_economics.budget_policy import MAX_BUDGET_POLICIES_PER_REPOSITORY
from ci_coordinator.ci_economics.budget_signal import BudgetSignal
from ci_coordinator.ci_economics.report_payload import ReportCounterPayload
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


class ConfigureBudgetPolicyBody(EconomicsSourceScopeBody):
    policy_key: BudgetKey = Field(validation_alias="policyKey")
    expected_revision: int = Field(
        validation_alias="expectedRevision", ge=0, lt=MAX_SAFE_JSON_INTEGER
    )
    configuration: BudgetConfigurationPayload
    operation_id: BudgetKey = Field(validation_alias="operationId")

    def to_command(self, actor: str) -> ConfigureBudgetPolicy:
        return ConfigureBudgetPolicy(
            self.scope,
            self.policy_key,
            self.expected_revision,
            self.configuration.to_configuration(),
            self.operation_id,
            actor,
        )


class BudgetPoliciesResponse(ResponseModel):
    schema_version: Literal["ci-economics-budget-policies/v1"] = "ci-economics-budget-policies/v1"
    installation_id: BudgetPositiveId
    repository_id: BudgetPositiveId
    policies: tuple[BudgetPolicyPayload, ...] = Field(max_length=MAX_BUDGET_POLICIES_PER_REPOSITORY)
    maximum_policies: Literal[16] = MAX_BUDGET_POLICIES_PER_REPOSITORY


class BudgetPolicyMutationResponse(ResponseModel):
    schema_version: Literal["ci-economics-budget-mutation/v1"] = "ci-economics-budget-mutation/v1"
    operation_id: BudgetKey
    outcome: Literal[
        "committed", "replayed", "revision_conflict", "operation_conflict", "capacity_reached"
    ]
    policy: BudgetPolicyPayload | None


class BudgetSignalResponse(ResponseModel):
    schema_version: Literal["ci-economics-budget-signal/v1"] = "ci-economics-budget-signal/v1"
    signal_id: BudgetDigest
    policy: BudgetPolicyPayload
    policy_digest: BudgetDigest
    source_id: BudgetDigest
    report_id: BudgetDigest
    report_digest: BudgetDigest
    measurement: ReportCounterPayload
    command_exit_code: int = Field(ge=-MAX_SAFE_JSON_INTEGER, le=MAX_SAFE_JSON_INTEGER)
    received_at: datetime
    retain_until: datetime
    outcome: ReportBudgetOutcome


class BudgetSignalsResponse(ResponseModel):
    schema_version: Literal["ci-economics-budget-signals/v1"] = "ci-economics-budget-signals/v1"
    installation_id: BudgetPositiveId
    repository_id: BudgetPositiveId
    items: tuple[BudgetSignalResponse, ...] = Field(max_length=100)
    next_cursor: BudgetDigest | None


def budget_signal_response(signal: BudgetSignal) -> BudgetSignalResponse:
    return BudgetSignalResponse(
        signal_id=signal.signal_id,
        policy=BudgetPolicyPayload.model_validate(signal.policy.canonical_mapping()),
        policy_digest=signal.policy.policy_digest,
        source_id=signal.source_id,
        report_id=signal.report_id,
        report_digest=signal.report_digest,
        measurement=ReportCounterPayload.model_validate(signal.measurement.canonical_mapping()),
        command_exit_code=signal.command_exit_code,
        received_at=signal.received_at,
        retain_until=signal.retain_until,
        outcome=signal.outcome,
    )
