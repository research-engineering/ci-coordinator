from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator

from ci_coordinator.ci_economics.archive_detail import ArchivedAttemptDetail
from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    ArchiveDetailState,
    DetailPolicyReference,
)
from ci_coordinator.ci_economics.archive_retention_payload import DetailRetentionPayload
from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptHeader,
    ArchivedJobStatistics,
    ArchiveInstant,
)
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.observation_payload import (
    ObservationPositiveId,
    ObservationTimestamp,
)
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel, JsonTuple
from ci_coordinator.config_control import RepositoryScope

MAX_HISTORY_READ_PAGE = 50
type HistoryReadKind = Literal["records", "jobs", "gaps", "detail"]


class HistoryReadQuery(EconomicsPayloadModel):
    installation_id: ObservationPositiveId = Field(alias="installationId")
    repository_id: ObservationPositiveId = Field(alias="repositoryId")
    generation: ObservationPositiveId
    kind: HistoryReadKind
    limit: int = Field(default=50, ge=1, le=MAX_HISTORY_READ_PAGE, strict=True)
    created_from: ObservationTimestamp | None = Field(default=None, alias="createdFrom")
    created_through: ObservationTimestamp | None = Field(default=None, alias="createdThrough")
    workflow_id: ObservationPositiveId | None = Field(default=None, alias="workflowId")
    workflow_run_id: ObservationPositiveId | None = Field(default=None, alias="workflowRunId")
    run_attempt: ObservationPositiveId | None = Field(default=None, alias="runAttempt")
    job_name: str | None = Field(default=None, alias="jobName", min_length=1, max_length=512)

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)

    @model_validator(mode="after")
    def admit_selection(self) -> Self:
        if (self.workflow_run_id is None) != (self.run_attempt is None):
            raise ValueError("history read requires both attempt coordinates")
        if self.kind in {"jobs", "detail"} and self.workflow_run_id is None:
            raise ValueError("job and detail reads require an exact attempt")
        if self.kind == "gaps" and self.workflow_run_id is not None:
            raise ValueError("gap reads use observation-identity pagination")
        if self.kind != "records" and any(
            value is not None
            for value in (self.created_from, self.created_through, self.workflow_id)
        ):
            raise ValueError("source-time and workflow filters apply only to records")
        if self.kind not in {"records", "jobs"} and self.job_name is not None:
            raise ValueError("job name filter applies only to records and jobs")
        if self.job_name is not None and (
            "\x00" in self.job_name or any(0xD800 <= ord(c) <= 0xDFFF for c in self.job_name)
        ):
            raise ValueError("job name requires Unicode scalar text")
        if (
            self.created_from is not None
            and self.created_through is not None
            and (
                datetime.fromisoformat(self.created_from)
                > datetime.fromisoformat(self.created_through)
            )
        ):
            raise ValueError("history read interval is reversed")
        return self


class HistoryReadKey(EconomicsPayloadModel):
    time: ArchiveInstant | None = None
    run: ObservationPositiveId | None = None
    attempt: ObservationPositiveId | None = None
    job: ObservationPositiveId | None = None
    gap: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def admit_key(self) -> Self:
        present = {
            name
            for name in ("time", "run", "attempt", "job", "gap")
            if getattr(self, name) is not None
        }
        if present not in ({"time", "run", "attempt"}, {"job"}, {"gap"}):
            raise ValueError("archive key requires exactly one read-grain identity")
        return self


class HistoryReadCursor(EconomicsPayloadModel):
    query_digest: str = Field(alias="queryDigest", pattern=r"^[0-9a-f]{64}$")
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    data_revision: ObservationPositiveId = Field(alias="dataRevision")
    observed_at: ArchiveInstant = Field(alias="observedAt")
    key: HistoryReadKey


class HistoryDetailView(EconomicsPayloadModel):
    state: ArchiveDetailState
    first_imported_at: ArchiveInstant | None = Field(alias="firstImportedAt")
    expires_at: ArchiveInstant | None = Field(alias="expiresAt")
    applied_policy: DetailRetentionPayload | None = Field(alias="appliedPolicy")
    policy_source: Literal["service_default", "repository_override"] | None = Field(
        alias="policySource"
    )
    policy_revision: ObservationPositiveId | None = Field(alias="policyRevision")
    content: Literal["not_imported", "expired", "unavailable_format"]

    @model_validator(mode="after")
    def admit_retention(self) -> Self:
        if (self.policy_source is None) != (self.policy_revision is None):
            raise ValueError("archive detail requires a complete policy reference")
        reference = None
        if self.policy_source is not None and self.policy_revision is not None:
            reference = DetailPolicyReference(self.policy_source, self.policy_revision)
        retention = ArchiveDetailRetention(
            self.state,
            self.first_imported_at,
            None if self.applied_policy is None else self.applied_policy.to_policy(),
            reference,
        )
        content = "unavailable_format" if self.state == "retained" else self.state
        if retention.expires_at != self.expires_at or self.content != content:
            raise ValueError("archive detail projection contradicts its retention")
        return self


class HistoryRecordSummary(EconomicsPayloadModel):
    header: ArchivedAttemptHeader
    job_count: int = Field(alias="jobCount", ge=0, le=2000)
    has_conflict: bool = Field(alias="hasConflict")
    first_imported_at: ArchiveInstant = Field(alias="firstImportedAt")
    detail: HistoryDetailView
    upstream_availability: Literal["not_checked"] = Field(
        default="not_checked", alias="upstreamAvailability"
    )

    @model_validator(mode="after")
    def admit_population(self) -> Self:
        self.header.admit_job_count(self.job_count)
        return self


type HistoryGapResolution = Literal[
    "not_evaluated", "missing", "retained_complete", "retained_incomplete", "retained_conflicting"
]


def history_gap_resolution(
    workflow_run_id: int | None, retained: HistoryRecordSummary | None
) -> HistoryGapResolution:
    if workflow_run_id is None:
        return "not_evaluated"
    if retained is None:
        return "missing"
    if retained.has_conflict or retained.header.population == "conflict":
        return "retained_conflicting"
    return (
        "retained_complete" if retained.header.population == "complete" else "retained_incomplete"
    )


class HistoryGapView(EconomicsPayloadModel):
    gap_id: str = Field(alias="gapId", pattern=r"^[0-9a-f]{64}$")
    recorded_at: ArchiveInstant = Field(alias="recordedAt")
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    reason: Literal[
        "provider_truncated",
        "provider_not_found",
        "job_population_incomplete",
        "incomparable",
        "provider_deferred",
        "retry_exhausted",
    ]
    workflow_run_id: ObservationPositiveId | None = Field(alias="workflowRunId")
    run_attempt: ObservationPositiveId | None = Field(alias="runAttempt")
    source_window: HistoryCursorPayload | None = Field(alias="sourceWindow")
    run_created_at: ArchiveInstant | None = Field(alias="runCreatedAt")
    retained: HistoryRecordSummary | None = None
    retry_supported: bool = Field(alias="retrySupported")
    resolution: HistoryGapResolution

    @model_validator(mode="after")
    def admit_current_evidence(self) -> Self:
        retained = self.retained
        if (
            (self.workflow_run_id is None) != (self.run_attempt is None)
            or (self.reason == "provider_truncated") != (self.workflow_run_id is None)
            or (self.source_window is None) != (self.run_created_at is not None)
        ):
            raise ValueError("gap source projection has contradictory identity fields")
        if self.retry_supported != (self.source_window is None and self.run_created_at is not None):
            raise ValueError("targeted retry requires complete durable recheck metadata")
        if self.run_created_at is not None and self.run_created_at > self.recorded_at:
            raise ValueError("gap source creation postdates its recorded observation")
        if retained is not None:
            attempt = retained.header.attempt
            if (attempt.workflow_run_id, attempt.run_attempt) != (
                self.workflow_run_id,
                self.run_attempt,
            ):
                raise ValueError("gap evidence names a different run or attempt")
            if (
                self.run_created_at is not None
                and self.run_created_at != retained.header.run_created_at
            ):
                raise ValueError("gap and retained evidence disagree on source creation")
        if self.resolution != history_gap_resolution(self.workflow_run_id, retained):
            raise ValueError("gap resolution contradicts its current retained summary")
        return self


class HistoryReadPage(EconomicsPayloadModel):
    query: HistoryReadQuery
    configuration_revision: ObservationPositiveId = Field(alias="configurationRevision")
    data_revision: ObservationPositiveId = Field(alias="dataRevision")
    observed_at: ArchiveInstant = Field(alias="observedAt")
    records: JsonTuple[HistoryRecordSummary] = Field(default=(), max_length=50)
    jobs: JsonTuple[ArchivedJobStatistics] = Field(default=(), max_length=50)
    gaps: JsonTuple[HistoryGapView] = Field(default=(), max_length=50)
    detail: HistoryDetailView | None = None
    detail_payload: ArchivedAttemptDetail | None = None
    next_key: HistoryReadKey | None = Field(default=None, alias="nextKey")

    @model_validator(mode="after")
    def admit_page(self) -> Self:
        query = self.query
        if query.kind == "records" and (
            self.jobs or self.gaps or self.detail is not None or self.detail_payload is not None
        ):
            raise ValueError("record page contains a foreign read projection")
        if query.kind == "gaps" and (
            self.records or self.jobs or self.detail is not None or self.detail_payload is not None
        ):
            raise ValueError("gap page contains a foreign read projection")
        if query.kind in {"jobs", "detail"} and len(self.records) != 1:
            raise ValueError("attempt drill-down requires exactly one parent")
        if query.kind == "jobs" and (
            self.gaps or self.detail is not None or self.detail_payload is not None
        ):
            raise ValueError("job page contains a foreign read projection")
        if query.kind == "detail" and (
            self.jobs
            or self.gaps
            or self.detail != self.records[0].detail
            or self.next_key is not None
        ):
            raise ValueError("detail projection contradicts its parent")
        record_keys = []
        for record in self.records:
            header = record.header
            attempt = header.attempt.to_attempt()
            if (
                attempt.scope != query.scope
                or max(record.first_imported_at, header.run_created_at) > self.observed_at
            ):
                raise ValueError("archive record crosses its observed scope")
            if query.workflow_run_id is not None and (
                attempt.workflow_run_id != query.workflow_run_id
                or attempt.run_attempt != query.run_attempt
            ):
                raise ValueError("archive record crosses selected attempt")
            if query.workflow_id is not None and header.workflow_id != query.workflow_id:
                raise ValueError("archive record crosses selected workflow")
            if (
                query.created_from is not None
                and header.run_created_at < datetime.fromisoformat(query.created_from)
            ) or (
                query.created_through is not None
                and header.run_created_at > datetime.fromisoformat(query.created_through)
            ):
                raise ValueError("archive record crosses selected source interval")
            record_keys.append(
                (header.run_created_at, attempt.workflow_run_id, attempt.run_attempt)
            )
        job_keys = [job.provider_job_id for job in self.jobs]
        gap_keys = [gap.gap_id for gap in self.gaps]
        if (
            record_keys != sorted(set(record_keys))
            or job_keys != sorted(set(job_keys))
            or gap_keys != sorted(set(gap_keys))
        ):
            raise ValueError("archive page is not a unique ordered population")
        if any(query.job_name is not None and job.name != query.job_name for job in self.jobs):
            raise ValueError("archive job crosses selected name")
        if any(
            gap.recorded_at > self.observed_at
            or gap.configuration_revision > self.configuration_revision
            or (
                gap.source_window is not None and gap.source_window.to_cursor().scope != query.scope
            )
            for gap in self.gaps
        ):
            raise ValueError("archive gap crosses observed scope or revision")
        if any(
            gap.retained is not None
            and (
                gap.retained.header.attempt.to_attempt().scope != query.scope
                or max(gap.retained.first_imported_at, gap.retained.header.run_created_at)
                > self.observed_at
            )
            for gap in self.gaps
        ):
            raise ValueError("gap retained summary crosses scope or observation time")
        keys = (
            job_keys if query.kind == "jobs" else gap_keys if query.kind == "gaps" else record_keys
        )
        if len(keys) > query.limit:
            raise ValueError("archive page exceeds selected limit")
        key = self.next_key
        if key is not None:
            last = (
                key.job
                if query.kind == "jobs"
                else key.gap
                if query.kind == "gaps"
                else (key.time, key.run, key.attempt)
            )
            if not keys or last != keys[-1]:
                raise ValueError("archive continuation is not the last visible key")
        return self


@dataclass(frozen=True, slots=True)
class HistoryReadRejected:
    reason: Literal["invalid_cursor", "stale_cursor", "not_found", "dataset_fenced"]

    def __post_init__(self) -> None:
        if self.reason not in {"invalid_cursor", "stale_cursor", "not_found", "dataset_fenced"}:
            raise ValueError("unknown archive read rejection")


class HistoryReadStore(Protocol):
    async def read_history(
        self, query: HistoryReadQuery, cursor: HistoryReadCursor | None
    ) -> HistoryReadPage | HistoryReadRejected: ...
