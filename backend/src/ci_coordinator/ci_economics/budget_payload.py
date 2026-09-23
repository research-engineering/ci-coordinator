from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.budget import ReportBudget
from ci_coordinator.ci_economics.budget_policy import (
    BudgetPolicyConfiguration,
    BudgetPolicySnapshot,
    BudgetSelector,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.ci_economics.reports import ReportCounter
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type BudgetKey = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]
type BudgetDigest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
type BudgetPositiveId = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]


class BudgetSelectorPayload(EconomicsPayloadModel):
    sample_key: BudgetKey = Field(alias="sampleKey")
    producer_digest: BudgetDigest = Field(alias="producerDigest")
    method: Literal["waited_children/v1"]
    runner_class_digest: BudgetDigest | None = Field(alias="runnerClassDigest")

    def to_selector(self) -> BudgetSelector:
        return BudgetSelector(self.sample_key, self.producer_digest, self.runner_class_digest)


class BudgetConfigurationPayload(EconomicsPayloadModel):
    enabled: bool
    selector: BudgetSelectorPayload
    counter: ReportCounter
    maximum_us: int = Field(alias="maximumUs", ge=0, le=MAX_SAFE_JSON_INTEGER)

    def to_configuration(self) -> BudgetPolicyConfiguration:
        return BudgetPolicyConfiguration(
            self.enabled, self.selector.to_selector(), ReportBudget(self.counter, self.maximum_us)
        )


class BudgetPolicyPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-budget-policy/v1"] = Field(alias="schemaVersion")
    installation_id: BudgetPositiveId = Field(alias="installationId")
    repository_id: BudgetPositiveId = Field(alias="repositoryId")
    policy_key: BudgetKey = Field(alias="policyKey")
    revision: BudgetPositiveId
    configuration: BudgetConfigurationPayload

    @model_validator(mode="after")
    def admit_policy(self) -> Self:
        _ = self.to_policy()
        return self

    def to_policy(self) -> BudgetPolicySnapshot:
        return BudgetPolicySnapshot(
            RepositoryScope(self.installation_id, self.repository_id),
            self.policy_key,
            self.revision,
            self.configuration.to_configuration(),
        )
