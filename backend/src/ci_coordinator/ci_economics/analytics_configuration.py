from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsModel,
    Count,
    PositiveId,
    PurposeEntry,
    PurposeMapping,
)
from ci_coordinator.ci_economics.payload_model import JsonTuple
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

PURPOSE_CONFIGURED_EVENT = "ci-analytics-purpose-configured/v1"
PURPOSE_PROVENANCE = "administrator-api/v1"
MAX_PURPOSE_BYTES = 262144


class PurposeSettingsQuery(AnalyticsModel):
    installation_id: PositiveId
    repository_id: PositiveId
    generation: PositiveId

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)


class PurposeSettingsSnapshot(PurposeSettingsQuery):
    revision: Count
    mapping: PurposeMapping | None

    @model_validator(mode="after")
    def consistent_mapping(self) -> Self:
        if self.mapping is None:
            if self.revision != 0:
                raise ValueError("missing mapping requires revision zero")
        elif (
            self.revision == 0
            or self.mapping.installation_id != self.installation_id
            or self.mapping.repository_id != self.repository_id
            or self.mapping.generation != self.generation
            or self.mapping.version != f"repository-settings:{self.revision}"
            or self.mapping.provenance != PURPOSE_PROVENANCE
        ):
            raise ValueError("purpose settings mapping identity differs")
        return self


class PurposeSettingsRequest(PurposeSettingsQuery):
    expected_revision: int = Field(ge=0, lt=MAX_SAFE_JSON_INTEGER)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    entries: JsonTuple[PurposeEntry] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_mapping(self) -> Self:
        if len({(entry.workflow_id, entry.job_name) for entry in self.entries}) != len(
            self.entries
        ):
            raise ValueError("purpose settings repeat a workflow/job key")
        return self

    def successor(self) -> PurposeSettingsSnapshot:
        revision = self.expected_revision + 1
        return PurposeSettingsSnapshot(
            installation_id=self.installation_id,
            repository_id=self.repository_id,
            generation=self.generation,
            revision=revision,
            mapping=PurposeMapping(
                installation_id=self.installation_id,
                repository_id=self.repository_id,
                generation=self.generation,
                version=f"repository-settings:{revision}",
                provenance=PURPOSE_PROVENANCE,
                entries=self.entries,
            ),
        )


class ConfigurePurposeSettings(PurposeSettingsRequest):
    actor: str = Field(min_length=1, max_length=512)

    @field_validator("actor")
    @classmethod
    def scalar_actor(cls, value: str) -> str:
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("purpose settings actor must be Unicode scalar text")
        return value

    @property
    def command_digest(self) -> str:
        return hash_object(self.model_dump(mode="json"))

    @property
    def audit_key(self) -> str:
        return (
            f"ci-analytics-purpose:{self.installation_id}:{self.repository_id}:{self.operation_id}"
        )


@dataclass(frozen=True, slots=True)
class PurposeSettingsSaved:
    snapshot: PurposeSettingsSnapshot
    replayed: bool


@dataclass(frozen=True, slots=True)
class PurposeSettingsConflict:
    reason: Literal[
        "revision_conflict", "operation_conflict", "generation_changed", "dataset_fenced"
    ]


type PurposeSettingsResult = PurposeSettingsSaved | PurposeSettingsConflict
