from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter

from ci_coordinator.ci_economics.archive_statistics import (
    ArchivedAttemptStatistics,
    ArchivedJobStatistics,
)
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.observation_payload import ObservationTimestamp
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import JOBS_PAGE_SIZE

_TIMESTAMP: TypeAdapter[str] = TypeAdapter(ObservationTimestamp)
_ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})


@dataclass(frozen=True, slots=True)
class HistoryAttemptHeader:
    statistics: ArchivedAttemptStatistics
    attempt_created_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryJobPage:
    total: int
    observed_ids: tuple[int, ...]
    terminal_jobs: tuple[ArchivedJobStatistics, ...]


def decode_history_header(
    body: bytes, cursor: HistoryAttemptCursor, *, run_created_at: datetime
) -> HistoryAttemptHeader | ProviderAttemptDeferred:
    raw = json_object_or_none(body)
    repository = object_or_none(raw.get("repository")) if raw is not None else None
    if raw is None or repository is None:
        return ProviderAttemptDeferred("provider_malformed")
    if "conclusion" not in raw:
        return ProviderAttemptDeferred("provider_malformed")
    try:
        attempt_created_at = datetime.fromisoformat(
            _TIMESTAMP.validate_python(raw.get("created_at"), strict=True)
        )
        header = ArchivedAttemptStatistics.model_validate(
            {
                "schemaVersion": "ci-economics-archive-statistics/v1",
                "attempt": {
                    "installationId": cursor.scope.installation_id,
                    "repositoryId": repository.get("id"),
                    "workflowRunId": raw.get("id"),
                    "runAttempt": raw.get("run_attempt"),
                    "headSha": raw.get("head_sha"),
                },
                "workflowId": raw.get("workflow_id"),
                "workflowPath": raw.get("path"),
                "workflowBlobSha": None,
                "event": raw.get("event"),
                "conclusion": raw["conclusion"],
                "runCreatedAt": run_created_at,
                "population": "unavailable",
                "providerJobTotal": None,
                "jobs": (),
            }
        )
    except ValueError:
        return ProviderAttemptDeferred("provider_malformed")
    identity = header.attempt.to_attempt()
    if (identity.scope, identity.workflow_run_id, identity.run_attempt) != (
        cursor.scope,
        cursor.workflow_run_id,
        cursor.next_attempt,
    ):
        return ProviderAttemptDeferred("provider_binding_mismatch")
    status = raw.get("status")
    if type(status) is not str:
        return ProviderAttemptDeferred("provider_malformed")
    if status in _ACTIVE_STATUSES and header.conclusion is None:
        return ProviderAttemptDeferred("provider_not_terminal")
    if status != "completed":
        return ProviderAttemptDeferred("provider_malformed")
    return HistoryAttemptHeader(header, attempt_created_at)


def decode_history_job_page(
    body: bytes, cursor: HistoryAttemptCursor, *, head_sha: str
) -> HistoryJobPage | ProviderAttemptDeferred:
    raw = json_object_or_none(body)
    total = None if raw is None else non_negative_safe_integer(raw.get("total_count"))
    raw_jobs = None if raw is None else raw.get("jobs")
    if total is None or type(raw_jobs) is not list or len(raw_jobs) > JOBS_PAGE_SIZE:
        return ProviderAttemptDeferred("provider_malformed")
    observed_ids: list[int] = []
    jobs: list[ArchivedJobStatistics] = []
    for raw_job in raw_jobs:
        value = object_or_none(raw_job)
        if (
            value is None
            or "conclusion" not in value
            or positive_safe_integer(value.get("run_id")) != cursor.workflow_run_id
            or value.get("head_sha") != head_sha
        ):
            return ProviderAttemptDeferred("provider_malformed")
        if "run_attempt" in value and (
            positive_safe_integer(value["run_attempt"]) != cursor.next_attempt
        ):
            return ProviderAttemptDeferred("provider_binding_mismatch")
        job_id = positive_safe_integer(value.get("id"))
        if job_id is None:
            return ProviderAttemptDeferred("provider_malformed")
        observed_ids.append(job_id)
        status = value.get("status")
        if type(status) is not str:
            return ProviderAttemptDeferred("provider_malformed")
        if status in _ACTIVE_STATUSES and value["conclusion"] is None:
            continue
        labels = value.get("labels")
        if status != "completed" or type(labels) is not list:
            return ProviderAttemptDeferred("provider_malformed")
        try:
            job = ArchivedJobStatistics.model_validate(
                {
                    "providerJobId": value.get("id"),
                    "name": value.get("name"),
                    "conclusion": value["conclusion"],
                    "createdAt": _timestamp(value.get("created_at")),
                    "startedAt": _timestamp(value.get("started_at")),
                    "completedAt": _timestamp(value.get("completed_at")),
                    "labels": tuple(labels),
                    "runnerId": _runner_id(value.get("runner_id")),
                    "runnerGroupId": _runner_id(value.get("runner_group_id")),
                }
            )
        except ValueError:
            return ProviderAttemptDeferred("provider_malformed")
        jobs.append(job.model_copy(update={"labels": tuple(sorted(job.labels))}))
    return HistoryJobPage(total, tuple(observed_ids), tuple(jobs))


def _runner_id(value: object) -> object:
    return None if type(value) is int and value == 0 else value


def _timestamp(value: object) -> datetime | None:
    return (
        None
        if value is None
        else datetime.fromisoformat(_TIMESTAMP.validate_python(value, strict=True))
    )
