from typing import Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object

type HistoryGapReason = Literal[
    "provider_truncated",
    "provider_not_found",
    "job_population_incomplete",
    "incomparable",
    "provider_deferred",
]


class HistoryGap(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-history-gap/v1"] = Field(alias="schemaVersion")
    generation: ObservationPositiveId
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    cursor: HistoryCursorPayload
    reason: HistoryGapReason
    workflow_run_id: ObservationPositiveId | None = Field(alias="workflowRunId")
    run_attempt: ObservationPositiveId | None = Field(alias="runAttempt")

    @model_validator(mode="after")
    def admit_subject(self) -> Self:
        _ = self.cursor.to_cursor()
        if (self.workflow_run_id is None) != (self.run_attempt is None):
            raise ValueError("history gap requires both or neither attempt identity fields")
        if (self.reason == "provider_truncated") != (self.workflow_run_id is None):
            raise ValueError("history gap reason must match its page or attempt subject")
        return self

    @property
    def scope(self) -> RepositoryScope:
        return self.cursor.to_cursor().scope

    @property
    def gap_id(self) -> str:
        return hash_object(self.model_dump(mode="json"))


class HistoryRecheckGap(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-history-recheck-gap/v1"] = Field(alias="schemaVersion")
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    workflow_run_id: ObservationPositiveId = Field(alias="workflowRunId")
    workflow_id: ObservationPositiveId = Field(alias="workflowId")
    run_attempt: ObservationPositiveId = Field(alias="runAttempt")
    run_created_at: ArchiveInstant = Field(alias="runCreatedAt")
    reason: Literal[
        "provider_not_found", "retry_exhausted", "job_population_incomplete", "incomparable"
    ]

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)

    @property
    def gap_id(self) -> str:
        return hash_object(self.model_dump(mode="json"))
