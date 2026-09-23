from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import Field, field_validator

from ci_coordinator.ci_economics._observation_values import positive_id, utc_time
from ci_coordinator.ci_economics.archive_retention import (
    DetailPolicyReference,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.archive_retention_payload import DetailRetentionPayload
from ci_coordinator.ci_economics.observation import MAX_OBSERVATION_WORKFLOW_IDS
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

MAX_HISTORY_DATASETS: Final = 256


class HistoryQuota(EconomicsPayloadModel):
    attempts: ObservationPositiveId
    jobs: ObservationPositiveId
    gaps: ObservationPositiveId
    canonical_bytes: ObservationPositiveId = Field(alias="canonicalBytes")


class HistoryUsage(EconomicsPayloadModel):
    attempts: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    jobs: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    gaps: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER)
    canonical_bytes: int = Field(alias="canonicalBytes", ge=0, le=MAX_SAFE_JSON_INTEGER)

    @classmethod
    def empty(cls) -> Self:
        return cls(attempts=0, jobs=0, gaps=0, canonicalBytes=0)

    def fits(self, quota: HistoryQuota) -> bool:
        quota = HistoryQuota.model_validate(quota)
        usage = HistoryUsage.model_validate(self)
        return (
            usage.attempts <= quota.attempts
            and usage.jobs <= quota.jobs
            and usage.gaps <= quota.gaps
            and usage.canonical_bytes <= quota.canonical_bytes
        )

    def reserve(self, addition: HistoryUsage, quota: HistoryQuota) -> HistoryUsage | None:
        prior = HistoryUsage.model_validate(self)
        addition = HistoryUsage.model_validate(addition)
        quota = HistoryQuota.model_validate(quota)
        if addition == HistoryUsage.empty():
            return prior
        values = (
            prior.attempts + addition.attempts,
            prior.jobs + addition.jobs,
            prior.gaps + addition.gaps,
            prior.canonical_bytes + addition.canonical_bytes,
        )
        limits = (quota.attempts, quota.jobs, quota.gaps, quota.canonical_bytes)
        if any(value > limit for value, limit in zip(values, limits, strict=True)):
            return None
        return HistoryUsage(
            attempts=values[0], jobs=values[1], gaps=values[2], canonicalBytes=values[3]
        )

    def release(self, removed: HistoryUsage) -> HistoryUsage:
        prior = HistoryUsage.model_validate(self)
        removed = HistoryUsage.model_validate(removed)
        return HistoryUsage(
            attempts=prior.attempts - removed.attempts,
            jobs=prior.jobs - removed.jobs,
            gaps=prior.gaps - removed.gaps,
            canonicalBytes=prior.canonical_bytes - removed.canonical_bytes,
        )


@dataclass(frozen=True, slots=True)
class HistoryDefaults:
    revision: int
    detail_retention: DetailRetentionPolicy
    updated_at: datetime

    def __post_init__(self) -> None:
        positive_id(self.revision, "history defaults revision")
        if type(self.detail_retention) is not DetailRetentionPolicy:
            raise TypeError("history defaults require an exact retention policy")
        object.__setattr__(self, "updated_at", utc_time(self.updated_at))


class HistoryConfiguration(EconomicsPayloadModel):
    enabled: bool
    workflow_ids: (
        Annotated[
            tuple[ObservationPositiveId, ...],
            Field(min_length=1, max_length=MAX_OBSERVATION_WORKFLOW_IDS),
        ]
        | None
    ) = Field(alias="workflowIds")
    detail_retention: DetailRetentionPayload | None = Field(alias="detailRetention")
    quota: HistoryQuota

    @field_validator("workflow_ids", mode="before")
    @classmethod
    def freeze_wire_array(cls, value: object) -> object:
        if type(value) is list:
            if len(value) > MAX_OBSERVATION_WORKFLOW_IDS:
                raise ValueError("history selector exceeds its workflow bound")
            return tuple(value)
        return value

    @field_validator("workflow_ids")
    @classmethod
    def admit_workflow_set(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        if value is None:
            return None
        if len(set(value)) != len(value):
            raise ValueError("history workflow identities must be distinct")
        return tuple(sorted(value))

    def selects(self, workflow_id: int) -> bool:
        if type(workflow_id) is not int or not 1 <= workflow_id <= MAX_SAFE_JSON_INTEGER:
            raise ValueError("history workflow identity must be a positive safe integer")
        return self.workflow_ids is None or workflow_id in self.workflow_ids


type HistoryDatasetState = Literal["active", "paused", "erasing", "erased"]


@dataclass(frozen=True, slots=True)
class HistoryDataset:
    scope: RepositoryScope
    generation: int
    configuration_revision: int
    data_revision: int
    configured_at: datetime
    state: HistoryDatasetState
    configuration: HistoryConfiguration
    usage: HistoryUsage

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("history dataset requires exact repository scope")
        for value in (self.generation, self.configuration_revision, self.data_revision):
            positive_id(value, "history dataset revision")
        object.__setattr__(self, "configured_at", utc_time(self.configured_at))
        object.__setattr__(
            self, "configuration", HistoryConfiguration.model_validate(self.configuration)
        )
        object.__setattr__(self, "usage", HistoryUsage.model_validate(self.usage))
        if self.state not in {"active", "paused", "erasing", "erased"}:
            raise ValueError("unknown history dataset state")
        if self.configuration.enabled != (self.state == "active"):
            raise ValueError("history configuration contradicts dataset write admission")
        if self.state == "erased" and self.usage != HistoryUsage.empty():
            raise ValueError("erased history must have released all occupied resources")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-history-dataset/v1",
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "generation": self.generation,
            "configurationRevision": self.configuration_revision,
            "dataRevision": self.data_revision,
            "configuredAt": self.configured_at.isoformat(),
            "state": self.state,
            "configuration": self.configuration.model_dump(mode="json"),
            "usage": self.usage.model_dump(mode="json"),
        }

    def resolve_detail_policy(
        self, defaults: HistoryDefaults
    ) -> tuple[DetailRetentionPolicy, DetailPolicyReference]:
        if type(defaults) is not HistoryDefaults:
            raise TypeError("history policy resolution requires admitted defaults")
        override = self.configuration.detail_retention
        if override is not None:
            return override.to_policy(), DetailPolicyReference(
                "repository_override", self.configuration_revision
            )
        return defaults.detail_retention, DetailPolicyReference(
            "service_default", defaults.revision
        )
