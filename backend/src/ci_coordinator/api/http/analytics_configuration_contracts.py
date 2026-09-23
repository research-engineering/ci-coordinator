import re
from typing import Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.ci_economics.analytics_configuration import (
    ConfigurePurposeSettings,
    PurposeSettingsQuery,
    PurposeSettingsSnapshot,
)
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsUnavailable,
    JobName,
    PositiveId,
    Purpose,
    PurposeEntry,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


class PurposeEntryRequest(RequestModel):
    workflow_id: PositiveId = Field(validation_alias="workflowId")
    job_name: JobName = Field(validation_alias="jobName")
    purposes: list[Purpose] = Field(min_length=1, max_length=5)


class PurposeConfigurationRequest(RequestModel):
    installation_id: PositiveId = Field(validation_alias="installationId")
    repository_id: PositiveId = Field(validation_alias="repositoryId")
    generation: PositiveId
    expected_revision: int = Field(
        validation_alias="expectedRevision", ge=0, lt=MAX_SAFE_JSON_INTEGER
    )
    operation_id: str = Field(
        validation_alias="operationId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    entries: list[PurposeEntryRequest] = Field(max_length=64)

    def command(self, actor: str) -> ConfigurePurposeSettings:
        return ConfigurePurposeSettings(
            installation_id=self.installation_id,
            repository_id=self.repository_id,
            generation=self.generation,
            expected_revision=self.expected_revision,
            operation_id=self.operation_id,
            actor=actor,
            entries=tuple(
                PurposeEntry(
                    workflow_id=entry.workflow_id,
                    job_name=entry.job_name,
                    purposes=tuple(entry.purposes),
                )
                for entry in self.entries
            ),
        )


class PurposeSettingsReadResponse(ResponseModel):
    outcome: Literal["available", "unavailable"]
    snapshot: PurposeSettingsSnapshot | None
    unavailable: AnalyticsUnavailable | None

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if (self.snapshot is not None) != (self.outcome == "available") or (
            self.unavailable is not None
        ) != (self.outcome == "unavailable"):
            raise ValueError("purpose settings response must have one outcome")
        return self


class PurposeSettingsWriteResponse(ResponseModel):
    operation_id: str
    outcome: Literal[
        "committed",
        "replayed",
        "revision_conflict",
        "operation_conflict",
        "generation_changed",
        "dataset_fenced",
    ]
    snapshot: PurposeSettingsSnapshot | None

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if (self.snapshot is not None) != (self.outcome in {"committed", "replayed"}):
            raise ValueError("purpose mutation snapshot contradicts outcome")
        return self


def settings_query(installation: str, repository: str, generation: str) -> PurposeSettingsQuery:
    if any(
        re.fullmatch(r"[1-9][0-9]{0,15}", value) is None
        for value in (installation, repository, generation)
    ):
        raise ValueError("purpose settings identity must be a positive decimal")
    return PurposeSettingsQuery(
        installation_id=int(installation), repository_id=int(repository), generation=int(generation)
    )
