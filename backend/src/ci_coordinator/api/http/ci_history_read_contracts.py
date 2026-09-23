from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.app.ci_history_read import HistoryReadResult
from ci_coordinator.ci_economics.archive_detail import ArchivedAttemptDetail
from ci_coordinator.ci_economics.archive_statistics import ArchivedJobStatistics
from ci_coordinator.ci_economics.history_read import (
    HistoryDetailView,
    HistoryGapView,
    HistoryReadQuery,
    HistoryRecordSummary,
)
from ci_coordinator.ci_economics.history_retention_commands import (
    HistoryRetentionOutcome,
    HistoryRetentionPreview,
)
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId


class HistoryReadResponse(ResponseModel):
    coverage: Literal["retained_local_rows"] = "retained_local_rows"
    provider_completeness: Literal["not_established"] = "not_established"
    query: HistoryReadQuery
    configuration_revision: ObservationPositiveId
    data_revision: ObservationPositiveId
    observed_at: datetime
    records: tuple[HistoryRecordSummary, ...] = Field(max_length=50)
    jobs: tuple[ArchivedJobStatistics, ...] = Field(max_length=50)
    gaps: tuple[HistoryGapView, ...] = Field(max_length=50)
    detail: HistoryDetailView | None
    next_cursor: str | None

    @classmethod
    def from_result(cls, result: HistoryReadResult) -> HistoryReadResponse:
        page = result.page
        return cls(
            query=page.query,
            configuration_revision=page.configuration_revision,
            data_revision=page.data_revision,
            observed_at=page.observed_at,
            records=page.records,
            jobs=page.jobs,
            gaps=page.gaps,
            detail=page.detail,
            next_cursor=result.next_cursor,
        )


class HistoryAttemptDetailResponse(ResponseModel):
    schema_version: Literal["ci-economics-history-attempt-detail/v1"] = (
        "ci-economics-history-attempt-detail/v1"
    )
    coverage: Literal["retained_local_rows"] = "retained_local_rows"
    provider_completeness: Literal["not_established"] = "not_established"
    query: HistoryReadQuery
    configuration_revision: ObservationPositiveId
    data_revision: ObservationPositiveId
    observed_at: datetime
    record: HistoryRecordSummary
    detail: HistoryDetailView
    detail_payload: ArchivedAttemptDetail | None

    @classmethod
    def from_result(cls, result: HistoryReadResult) -> HistoryAttemptDetailResponse:
        page = result.page
        if page.query.kind != "detail" or len(page.records) != 1 or page.detail is None:
            raise ValueError("history detail response requires one detail record")
        return cls(
            query=page.query,
            configuration_revision=page.configuration_revision,
            data_revision=page.data_revision,
            observed_at=page.observed_at,
            record=page.records[0],
            detail=page.detail,
            detail_payload=page.detail_payload,
        )


class HistoryRetentionPreviewResponse(ResponseModel):
    preview: HistoryRetentionPreview
    reviewed_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class HistoryRetentionResultResponse(ProjectedResponseModel):
    outcome: HistoryRetentionOutcome
    operation_id: str
    preview: HistoryRetentionPreview | None
    data_revision: ObservationPositiveId | None


class HistoryReadErrorResponse(ResponseModel):
    ok: Literal[False] = False
    error: Literal[
        "invalid_request",
        "invalid_cursor",
        "stale_cursor",
        "not_found",
        "dataset_fenced",
        "unauthenticated",
        "forbidden",
        "unavailable",
        "overloaded",
    ]
