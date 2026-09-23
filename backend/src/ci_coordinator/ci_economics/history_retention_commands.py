from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator

from ci_coordinator.ci_economics.history_read import HistoryDetailView
from ci_coordinator.ci_economics.observation_payload import (
    ObservationPositiveId,
    ObservationTimestamp,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel, JsonTuple
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object

HISTORY_RETENTION_EVENT = "ci-economics-history-retention-applied/v1"


class HistoryAttemptKey(EconomicsPayloadModel):
    workflow_run_id: ObservationPositiveId = Field(alias="workflowRunId")
    run_attempt: ObservationPositiveId = Field(alias="runAttempt")


class HistoryRetentionSelection(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    data_revision: ObservationPositiveId = Field(alias="dataRevision")
    default_revision: ObservationPositiveId = Field(alias="defaultRevision")
    imported_through: ObservationTimestamp = Field(alias="importedThrough")
    action: Literal["apply_policy", "erase_details"]
    keys: Annotated[tuple[HistoryAttemptKey, ...], Field(min_length=1, max_length=100)]

    @field_validator("keys", mode="before")
    @classmethod
    def freeze_keys(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def admit_keys(self) -> Self:
        coordinates = tuple((key.workflow_run_id, key.run_attempt) for key in self.keys)
        if coordinates != tuple(sorted(set(coordinates))):
            raise ValueError("retention selection requires unique ordered attempt keys")
        return self

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)


class HistoryRetentionEffect(EconomicsPayloadModel):
    key: HistoryAttemptKey
    before: HistoryDetailView
    after: HistoryDetailView
    payload_bytes: int = Field(alias="payloadBytes", ge=0, le=8_388_608)
    delete_payload: bool = Field(alias="deletePayload")


class HistoryRetentionPreview(EconomicsPayloadModel):
    selection: HistoryRetentionSelection
    effects: JsonTuple[HistoryRetentionEffect] = Field(min_length=1, max_length=100)
    released_bytes: int = Field(alias="releasedBytes", ge=0, le=838_860_800)
    deleted_details: int = Field(alias="deletedDetails", ge=0, le=100)
    statistics_preserved: Literal[True] = Field(default=True, alias="statisticsPreserved")

    @model_validator(mode="after")
    def admit_effects(self) -> Self:
        if tuple(effect.key for effect in self.effects) != self.selection.keys:
            raise ValueError("retention effects differ from the exact selection")
        if self.released_bytes != sum(
            e.payload_bytes for e in self.effects if e.delete_payload
        ) or (self.deleted_details != sum(e.delete_payload for e in self.effects)):
            raise ValueError("retention accounting differs from the selected effects")
        if any(e.before.first_imported_at != e.after.first_imported_at for e in self.effects):
            raise ValueError("retention cannot renew first import")
        return self

    @property
    def review_digest(self) -> str:
        return hash_object(self.model_dump(mode="json"))


class HistoryRetentionRequest(EconomicsPayloadModel):
    selection: HistoryRetentionSelection
    reviewed_digest: str = Field(alias="reviewedDigest", pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(
        alias="operationId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )


class ApplyHistoryRetention(HistoryRetentionRequest):
    actor: str = Field(min_length=1, max_length=512)

    @field_validator("actor")
    @classmethod
    def admit_actor(cls, value: str) -> str:
        if "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("retention actor requires Unicode scalar text")
        return value

    @classmethod
    def from_request(cls, request: HistoryRetentionRequest, *, actor: str) -> Self:
        request = HistoryRetentionRequest.model_validate(request)
        return cls.model_validate({**request.model_dump(), "actor": actor})

    @property
    def command_digest(self) -> str:
        return hash_object(self.model_dump(mode="json"))

    @property
    def audit_key(self) -> str:
        scope = self.selection.scope
        return (
            f"history-retention:{scope.installation_id}:{scope.repository_id}:{self.operation_id}"
        )


type HistoryRetentionOutcome = Literal[
    "committed", "replayed", "revision_conflict", "operation_conflict", "review_conflict"
]


class HistoryRetentionResult(EconomicsPayloadModel):
    outcome: HistoryRetentionOutcome
    operation_id: str = Field(alias="operationId")
    preview: HistoryRetentionPreview | None
    data_revision: ObservationPositiveId | None = Field(alias="dataRevision")

    @model_validator(mode="after")
    def admit_result(self) -> Self:
        success = self.outcome in {"committed", "replayed"}
        if success != (self.preview is not None) or success != (self.data_revision is not None):
            raise ValueError("retention receipt contradicts its outcome")
        return self


class HistoryRetentionStore(Protocol):
    async def preview_retention(
        self, selection: HistoryRetentionSelection
    ) -> HistoryRetentionPreview | None: ...

    async def apply_retention(self, command: ApplyHistoryRetention) -> HistoryRetentionResult: ...
