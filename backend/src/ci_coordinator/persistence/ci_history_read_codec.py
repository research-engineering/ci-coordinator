from collections.abc import Mapping
from datetime import datetime

from ci_coordinator.ci_economics.archive_detail import (
    MAX_HISTORY_DETAIL_BYTES,
    ArchivedAttemptDetail,
    validate_history_detail,
)
from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_HEADER_BYTES,
    MAX_HISTORY_JOB_BYTES,
    encode_archive_job,
)
from ci_coordinator.ci_economics.archive_retention import expire_archive_detail
from ci_coordinator.ci_economics.archive_retention_payload import DETAIL_RETENTION_ADAPTER
from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptHeader,
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
)
from ci_coordinator.ci_economics.history_gap import HistoryGap, HistoryRecheckGap
from ci_coordinator.ci_economics.history_read import (
    HistoryDetailView,
    HistoryGapView,
    HistoryRecordSummary,
    history_gap_resolution,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.canonical_row import decode_canonical_object, require_bytes
from ci_coordinator.persistence.ci_history_gap_store import MAX_HISTORY_GAP_BYTES
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_retention


def read_history_summary(row: Mapping[str, object], *, now: datetime) -> HistoryRecordSummary:
    raw = require_bytes(row.get("header_canonical"), "archive header")
    decode_canonical_object(raw, maximum_bytes=MAX_HISTORY_HEADER_BYTES, context="archive header")
    header = ArchivedAttemptHeader.model_validate_json(raw)
    attempt = header.attempt.to_attempt()
    expected = {
        "installation_id": attempt.scope.installation_id,
        "repository_id": attempt.scope.repository_id,
        "workflow_run_id": attempt.workflow_run_id,
        "run_attempt": attempt.run_attempt,
        "head_sha": attempt.head_sha,
        "workflow_id": header.workflow_id,
        "run_created_at": header.run_created_at,
    }
    match_read_columns(row, expected)
    return HistoryRecordSummary.model_validate(
        {
            "header": header,
            "jobCount": row.get("job_count"),
            "hasConflict": row.get("has_conflict"),
            "firstImportedAt": row.get("first_imported_at"),
            "detail": read_history_detail(row, now=now),
        }
    )


def read_history_detail(row: Mapping[str, object], *, now: datetime) -> HistoryDetailView:
    prior = decode_history_retention(row)
    if prior.first_imported_at is not None and prior.first_imported_at > now:
        raise ValueError("archive detail import postdates observation")
    retention = expire_archive_detail(prior, now=now)
    reference = retention.applied_reference
    return HistoryDetailView(
        state=retention.state,
        firstImportedAt=retention.first_imported_at,
        expiresAt=retention.expires_at,
        appliedPolicy=None
        if retention.applied_policy is None
        else DETAIL_RETENTION_ADAPTER.validate_python(retention.applied_policy.canonical_mapping()),
        policySource=None if reference is None else reference.source,
        policyRevision=None if reference is None else reference.revision,
        content="unavailable_format" if retention.state == "retained" else retention.state,
    )


def read_history_detail_payload(
    parent: Mapping[str, object],
    detail: Mapping[str, object] | None,
    statistics: ArchivedAttemptStatistics,
    *,
    now: datetime,
) -> ArchivedAttemptDetail | None:
    if detail is None:
        return None
    if any(detail.get(name) != parent.get(name) for name in _PARENT_KEY):
        return None
    try:
        match_read_columns(
            parent,
            {
                "installation_id": statistics.attempt.installation_id,
                "repository_id": statistics.attempt.repository_id,
                "workflow_run_id": statistics.attempt.workflow_run_id,
                "run_attempt": statistics.attempt.run_attempt,
                "head_sha": statistics.attempt.head_sha,
                "workflow_id": statistics.workflow_id,
                "run_created_at": statistics.run_created_at,
                "job_count": len(statistics.jobs),
                "has_conflict": False,
            },
        )
        retention = expire_archive_detail(decode_history_retention(parent), now=now)
        if retention.state != "retained":
            return None
        raw = require_bytes(detail.get("detail_canonical"), "archive detail")
        mapping = decode_canonical_object(
            raw, maximum_bytes=MAX_HISTORY_DETAIL_BYTES, context="archive detail"
        )
        if mapping.get("schemaVersion") != "ci-economics-archive-detail/v1":
            return None
        payload = ArchivedAttemptDetail.model_validate_json(raw)
        return validate_history_detail(statistics, payload)
    except (TypeError, ValueError, OverflowError):
        return None


def read_history_job(row: Mapping[str, object]) -> ArchivedJobStatistics:
    raw = require_bytes(row.get("job_canonical"), "archive job")
    decode_canonical_object(raw, maximum_bytes=MAX_HISTORY_JOB_BYTES, context="archive job")
    job = ArchivedJobStatistics.model_validate_json(raw)
    match_read_columns(
        row,
        {
            "provider_job_id": job.provider_job_id,
            "name": job.name,
            "conclusion": job.conclusion,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "job_canonical": encode_archive_job(job),
        },
    )
    return job


def read_history_gap(
    row: Mapping[str, object],
    scope: RepositoryScope,
    generation: int,
    *,
    retained: HistoryRecordSummary | None = None,
) -> HistoryGapView:
    raw = require_bytes(row.get("gap_canonical"), "archive gap")
    mapping = decode_canonical_object(
        raw, maximum_bytes=MAX_HISTORY_GAP_BYTES, context="archive gap"
    )
    gap = (
        HistoryGap.model_validate_json(raw)
        if mapping.get("schemaVersion") == "ci-economics-history-gap/v1"
        else HistoryRecheckGap.model_validate_json(raw)
    )
    if gap.scope != scope or gap.generation != generation or gap.gap_id != row.get("gap_id"):
        raise ValueError("archive gap contradicts its scope or identity")
    if (
        retained is not None
        and isinstance(gap, HistoryRecheckGap)
        and gap.workflow_id != retained.header.workflow_id
    ):
        raise ValueError("gap and retained summary disagree on workflow identity")
    return HistoryGapView.model_validate(
        {
            "gapId": gap.gap_id,
            "recordedAt": row.get("recorded_at"),
            "configurationRevision": gap.configuration_revision,
            "reason": gap.reason,
            "workflowRunId": gap.workflow_run_id,
            "runAttempt": gap.run_attempt,
            "sourceWindow": gap.cursor if isinstance(gap, HistoryGap) else None,
            "runCreatedAt": gap.run_created_at if isinstance(gap, HistoryRecheckGap) else None,
            "retained": retained,
            "retrySupported": isinstance(gap, HistoryRecheckGap),
            "resolution": history_gap_resolution(gap.workflow_run_id, retained),
        }
    )


def match_read_columns(row: Mapping[str, object], expected: Mapping[str, object]) -> None:
    for name, value in expected.items():
        actual = row.get(name)
        if type(value) is bytes:
            actual = require_bytes(actual, "archive column")
        if type(actual) is not type(value) or actual != value:
            raise ValueError("archive projection contradicts retained canonical fields")


_PARENT_KEY = (
    "installation_id",
    "repository_id",
    "generation",
    "workflow_run_id",
    "run_attempt",
)
