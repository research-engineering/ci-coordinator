from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.discovery import (
    DISCOVERY_PAGE_SIZE,
    DiscoveryPageTermination,
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.history_checkpoint import HistoryCheckpoint, HistoryPendingPage
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.observation_payload import (
    ObservationDigest,
    ObservationPositiveId,
    ObservationTimestamp,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.ci_economics.report_payload import ReportAttemptPayload
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


class _SourcePayload(EconomicsPayloadModel):
    attempt: ReportAttemptPayload
    run_created_at: ObservationTimestamp = Field(alias="runCreatedAt")
    provider_api_version: str = Field(alias="providerApiVersion", min_length=10, max_length=10)
    source_evidence_digest: ObservationDigest = Field(alias="sourceEvidenceDigest")
    workflow_id: ObservationPositiveId = Field(alias="workflowId")

    def to_source(self) -> ProviderRunCollectionSource:
        return ProviderRunCollectionSource(
            self.attempt.to_attempt(),
            datetime.fromisoformat(self.run_created_at),
            self.provider_api_version,
            self.source_evidence_digest,
        )


class _PendingPayload(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    window_from: ObservationTimestamp = Field(alias="windowFrom")
    window_through: ObservationTimestamp = Field(alias="windowThrough")
    page_number: int = Field(alias="pageNumber", ge=1, le=10)
    provider_total: int = Field(alias="providerTotal", ge=0, le=MAX_SAFE_JSON_INTEGER)
    sources: Annotated[list[_SourcePayload], Field(min_length=1, max_length=DISCOVERY_PAGE_SIZE)]
    termination: DiscoveryPageTermination
    run_index: int = Field(alias="runIndex", ge=0, lt=DISCOVERY_PAGE_SIZE)
    next_attempt: ObservationPositiveId = Field(alias="nextAttempt")

    def to_pending(self) -> HistoryPendingPage:
        page = ProviderRunDiscoveryPage(
            RepositoryScope(self.installation_id, self.repository_id),
            RunDiscoveryWindow(
                datetime.fromisoformat(self.window_from),
                datetime.fromisoformat(self.window_through),
            ),
            self.page_number,
            self.provider_total,
            tuple(source.to_source() for source in self.sources),
            self.termination,
        )
        return HistoryPendingPage(
            ProviderObservationPage(page, tuple(source.workflow_id for source in self.sources)),
            self.run_index,
            self.next_attempt,
        )


class HistoryCheckpointPayload(EconomicsPayloadModel):
    schema_version: Literal["ci-economics-history-checkpoint/v1"] = Field(alias="schemaVersion")
    cursor: HistoryCursorPayload
    pending: _PendingPayload | None
    complete: bool

    @model_validator(mode="after")
    def admit_checkpoint(self) -> Self:
        _ = self.to_checkpoint()
        return self

    def to_checkpoint(self) -> HistoryCheckpoint:
        return HistoryCheckpoint(
            self.cursor.to_cursor(),
            None if self.pending is None else self.pending.to_pending(),
            self.complete,
        )

    @classmethod
    def from_checkpoint(cls, checkpoint: HistoryCheckpoint) -> Self:
        if type(checkpoint) is not HistoryCheckpoint:
            raise TypeError("history serialization requires an exact checkpoint")
        pending = checkpoint.pending
        page = None if pending is None else pending.observed.page
        return cls.model_validate(
            {
                "schemaVersion": "ci-economics-history-checkpoint/v1",
                "cursor": checkpoint.cursor.canonical_mapping(),
                "complete": checkpoint.complete,
                "pending": None
                if pending is None or page is None
                else {
                    "installationId": page.scope.installation_id,
                    "repositoryId": page.scope.repository_id,
                    "windowFrom": page.window.created_from.isoformat(),
                    "windowThrough": page.window.created_through.isoformat(),
                    "pageNumber": page.page_number,
                    "providerTotal": page.provider_total,
                    "sources": [
                        {
                            "attempt": source.attempt.canonical_mapping(),
                            "runCreatedAt": source.run_created_at.isoformat(),
                            "providerApiVersion": source.provider_api_version,
                            "sourceEvidenceDigest": source.source_evidence_digest,
                            "workflowId": workflow_id,
                        }
                        for source, workflow_id in zip(
                            page.sources, pending.observed.workflow_ids, strict=True
                        )
                    ],
                    "termination": page.termination,
                    "runIndex": pending.run_index,
                    "nextAttempt": pending.next_attempt,
                },
            }
        )
