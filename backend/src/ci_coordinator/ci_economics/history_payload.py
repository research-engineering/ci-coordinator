from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.discovery import MAX_DISCOVERY_PAGES, RunDiscoveryWindow
from ci_coordinator.ci_economics.history_configuration import (
    HistoryConfiguration,
    HistoryDataset,
    HistoryDatasetState,
    HistoryUsage,
)
from ci_coordinator.ci_economics.history_cursor import HistoryCursor
from ci_coordinator.ci_economics.observation_payload import (
    ObservationPositiveId,
    ObservationTimestamp,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope


class HistoryCursorPayload(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    created_from: ObservationTimestamp = Field(alias="createdFrom")
    created_through: ObservationTimestamp = Field(alias="createdThrough")
    window_from: ObservationTimestamp = Field(alias="windowFrom")
    window_through: ObservationTimestamp = Field(alias="windowThrough")
    page_number: int = Field(alias="pageNumber", ge=1, le=MAX_DISCOVERY_PAGES)
    cycle_started_at: ObservationTimestamp = Field(alias="cycleStartedAt")

    @model_validator(mode="after")
    def admit_cursor(self) -> Self:
        _ = self.to_cursor()
        return self

    def to_cursor(self) -> HistoryCursor:
        return HistoryCursor(
            RepositoryScope(self.installation_id, self.repository_id),
            datetime.fromisoformat(self.created_from),
            datetime.fromisoformat(self.created_through),
            RunDiscoveryWindow(
                datetime.fromisoformat(self.window_from),
                datetime.fromisoformat(self.window_through),
            ),
            self.page_number,
            datetime.fromisoformat(self.cycle_started_at),
        )


class HistoryDatasetPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-history-dataset/v1"] = Field(alias="schemaVersion")
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    data_revision: ObservationPositiveId = Field(alias="dataRevision")
    configured_at: ObservationTimestamp = Field(alias="configuredAt")
    state: HistoryDatasetState
    configuration: HistoryConfiguration
    usage: HistoryUsage

    @model_validator(mode="after")
    def admit_dataset(self) -> Self:
        _ = self.to_dataset()
        return self

    def to_dataset(self) -> HistoryDataset:
        return HistoryDataset(
            RepositoryScope(self.installation_id, self.repository_id),
            self.generation,
            self.configuration_revision,
            self.data_revision,
            datetime.fromisoformat(self.configured_at),
            self.state,
            self.configuration,
            self.usage,
        )
