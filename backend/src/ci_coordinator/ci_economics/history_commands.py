from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from ci_coordinator.ci_economics.history_configuration import HistoryConfiguration, HistoryDataset
from ci_coordinator.ci_economics.observation_payload import (
    ObservationPositiveId,
    ObservationTimestamp,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

HISTORY_CONFIGURED_EVENT_TYPE: Final = "ci-economics-history-configured/v1"


class HistoryConfigurationRequest(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    expected_revision: int = Field(alias="expectedRevision", ge=0, lt=MAX_SAFE_JSON_INTEGER)
    configuration: HistoryConfiguration
    initial_created_from: ObservationTimestamp | None = Field(alias="initialCreatedFrom")
    rescan: bool
    operation_id: str = Field(
        alias="operationId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )

    @field_validator("initial_created_from")
    @classmethod
    def admit_initial_time(cls, value: str | None) -> str | None:
        if value is not None and datetime.fromisoformat(value).microsecond:
            raise ValueError("initial history boundary requires second precision")
        return value

    @model_validator(mode="after")
    def admit_population_operation(self) -> Self:
        initial = self.expected_revision == 0
        if initial != (self.initial_created_from is not None):
            raise ValueError("initial history alone requires its population lower bound")
        if initial and self.rescan:
            raise ValueError("rescan requires an existing history dataset")
        return self

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)


class ConfigureHistory(HistoryConfigurationRequest):
    actor: str = Field(min_length=1, max_length=512)

    @classmethod
    def from_request(cls, request: HistoryConfigurationRequest, *, actor: str) -> Self:
        admitted = HistoryConfigurationRequest.model_validate(request)
        return cls.model_validate({**admitted.model_dump(), "actor": actor})

    @field_validator("actor")
    @classmethod
    def admit_actor(cls, value: str) -> str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("history actor must be bounded Unicode scalar text")
        return value

    @property
    def command_digest(self) -> str:
        return hash_object(self.model_dump(mode="json"))

    @property
    def audit_key(self) -> str:
        return (
            f"ci-economics-history:{self.installation_id}:{self.repository_id}:{self.operation_id}"
        )


@dataclass(frozen=True, slots=True)
class HistoryConfigured:
    snapshot: HistoryDataset
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.snapshot) is not HistoryDataset or type(self.replayed) is not bool:
            raise TypeError("history receipt requires an exact snapshot and replay disposition")


@dataclass(frozen=True, slots=True)
class HistoryConfigurationConflict:
    reason: Literal["revision_conflict", "operation_conflict", "capacity_reached", "dataset_fenced"]

    def __post_init__(self) -> None:
        if self.reason not in {
            "revision_conflict",
            "operation_conflict",
            "capacity_reached",
            "dataset_fenced",
        }:
            raise ValueError("unknown history configuration conflict")


@dataclass(frozen=True, slots=True)
class HistoryConfigurationInvalid:
    reason: Literal["invalid_population"] = "invalid_population"

    def __post_init__(self) -> None:
        if self.reason != "invalid_population":
            raise ValueError("unknown invalid history configuration")


type HistoryConfigurationResult = (
    HistoryConfigured | HistoryConfigurationConflict | HistoryConfigurationInvalid
)
