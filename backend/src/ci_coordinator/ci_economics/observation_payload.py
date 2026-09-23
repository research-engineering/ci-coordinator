from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.observation import (
    MAX_OBSERVATION_BACKFILL_DAYS,
    MAX_OBSERVATION_WORKFLOW_IDS,
    ObservationConfiguration,
    ObservationSnapshot,
)
from ci_coordinator.ci_economics.observation_gaps import ObservationGap, ObservationGapReason
from ci_coordinator.ci_economics.observation_scan import ObservationLane
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type ObservationPositiveId = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_JSON_INTEGER)]
type ObservationDigest = Annotated[
    str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]
type ObservationTimestamp = Annotated[
    str,
    Field(
        min_length=20,
        max_length=32,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|\+00:00)$",
    ),
]


class ObservationSelectorPayload(EconomicsPayloadModel):
    kind: Literal["all", "selected"]
    workflow_ids: (
        Annotated[
            list[ObservationPositiveId],
            Field(min_length=1, max_length=MAX_OBSERVATION_WORKFLOW_IDS),
        ]
        | None
    ) = Field(alias="workflowIds")

    @model_validator(mode="after")
    def admit_selector(self) -> Self:
        if (self.kind == "all") != (self.workflow_ids is None):
            raise ValueError("workflow selector kind and identities contradict")
        if self.workflow_ids is not None and len(set(self.workflow_ids)) != len(self.workflow_ids):
            raise ValueError("workflow selector identities must be unique")
        return self


class ObservationConfigurationPayload(EconomicsPayloadModel):
    enabled: bool
    selector: ObservationSelectorPayload
    backfill_days: int = Field(alias="backfillDays", ge=0, le=MAX_OBSERVATION_BACKFILL_DAYS)

    def to_configuration(self) -> ObservationConfiguration:
        return ObservationConfiguration(
            self.enabled,
            None if self.selector.workflow_ids is None else tuple(self.selector.workflow_ids),
            self.backfill_days,
        )


class ObservationSnapshotPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-observation/v1"] = Field(alias="schemaVersion")
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    revision: ObservationPositiveId
    configuration: ObservationConfigurationPayload
    configured_at: ObservationTimestamp = Field(alias="configuredAt")

    @model_validator(mode="after")
    def admit_snapshot(self) -> Self:
        _ = self.to_snapshot()
        return self

    def to_snapshot(self) -> ObservationSnapshot:
        return ObservationSnapshot(
            RepositoryScope(self.installation_id, self.repository_id),
            self.revision,
            self.configuration.to_configuration(),
            datetime.fromisoformat(self.configured_at),
        )


class ObservationGapPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-observation-gap/v1"] = Field(alias="schemaVersion")
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    config_revision: ObservationPositiveId = Field(alias="configRevision")
    selector_digest: ObservationDigest = Field(alias="selectorDigest")
    lane: ObservationLane
    cycle_started_at: ObservationTimestamp = Field(alias="cycleStartedAt")
    created_from: ObservationTimestamp = Field(alias="createdFrom")
    created_through: ObservationTimestamp = Field(alias="createdThrough")
    reason: ObservationGapReason

    @model_validator(mode="after")
    def admit_gap(self) -> Self:
        _ = self.to_gap()
        return self

    def to_gap(self) -> ObservationGap:
        return ObservationGap(
            RepositoryScope(self.installation_id, self.repository_id),
            self.config_revision,
            self.selector_digest,
            self.lane,
            datetime.fromisoformat(self.cycle_started_at),
            datetime.fromisoformat(self.created_from),
            datetime.fromisoformat(self.created_through),
            self.reason,
        )
