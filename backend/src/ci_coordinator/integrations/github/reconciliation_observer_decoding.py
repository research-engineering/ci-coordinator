"""Strict decoding for untrusted GitHub reconciliation responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

from ci_coordinator.ci_economics.model import (
    MAX_JOB_LABEL_CODE_POINTS,
    MAX_JOB_LABELS,
    MAX_JOB_NAME_CODE_POINTS,
    MAX_RUNNER_TEXT_CODE_POINTS,
    RunnerIdentity,
    WorkflowConclusion,
)
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none as _json_object,
)
from ci_coordinator.integrations.github._response_decoding import (
    non_negative_safe_integer as _non_negative_safe_integer,
)
from ci_coordinator.integrations.github._response_decoding import (
    object_or_none as _object,
)
from ci_coordinator.integrations.github._response_decoding import (
    positive_safe_integer as _positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    JOBS_PAGE_SIZE,
)
from ci_coordinator.reconciliation.observation import SignalStatus

_ACTIVE_JOB_STATUSES: Final = frozenset(
    {"queued", "in_progress", "waiting", "requested", "pending"}
)
_DIRECT_CONCLUSIONS: Final = frozenset(
    {"success", "failure", "cancelled", "timed_out", "skipped", "neutral"}
)
_UNKNOWN_FAILURE_CONCLUSIONS: Final = frozenset({"action_required", "stale", "startup_failure"})


@dataclass(frozen=True, slots=True)
class ProviderJob:
    job_id: int
    name: str
    status: SignalStatus
    conclusion: WorkflowConclusion | None
    head_sha: str
    started_at: datetime | None
    completed_at: datetime | None
    labels: tuple[str, ...]
    runner: RunnerIdentity


@dataclass(frozen=True, slots=True)
class JobPage:
    total_count: int
    jobs: tuple[ProviderJob, ...]


def decode_repository(body: bytes) -> tuple[int, GitHubRepository] | None:
    """Decode and cross-check a repository identity response."""
    value = _json_object(body)
    if value is None:
        return None
    repository_id = _positive_safe_integer(value.get("id"))
    name = _non_empty_string(value.get("name"))
    full_name = _non_empty_string(value.get("full_name"))
    owner = _object(value.get("owner"))
    owner_login = _non_empty_string(owner.get("login")) if owner is not None else None
    if (
        repository_id is None
        or name is None
        or owner_login is None
        or full_name != f"{owner_login}/{name}"
    ):
        return None
    try:
        return repository_id, GitHubRepository(owner_login, name)
    except ValueError:
        return None


def decode_job_page(
    body: bytes,
    *,
    expected_run_id: int,
    expected_head_sha: str,
) -> JobPage | None:
    """Decode one bounded jobs page bound to an exact workflow run."""
    page = decode_job_page_facts(
        body, expected_run_id=expected_run_id, expected_head_sha=expected_head_sha
    )
    if page is None or any(
        job.started_at is not None
        and job.completed_at is not None
        and job.started_at > job.completed_at
        for job in page.jobs
    ):
        return None
    return page


def decode_job_page_facts(
    body: bytes,
    *,
    expected_run_id: int,
    expected_head_sha: str,
) -> JobPage | None:
    value = _json_object(body)
    if value is None:
        return None
    total_count = _non_negative_safe_integer(value.get("total_count"))
    raw_jobs = value.get("jobs")
    if total_count is None or not isinstance(raw_jobs, list) or len(raw_jobs) > JOBS_PAGE_SIZE:
        return None
    jobs: list[ProviderJob] = []
    for raw_job in raw_jobs:
        job = _decode_job(
            raw_job,
            expected_run_id=expected_run_id,
            expected_head_sha=expected_head_sha,
        )
        if job is None:
            return None
        jobs.append(job)
    return JobPage(total_count=total_count, jobs=tuple(jobs))


def _decode_job(
    value: object,
    *,
    expected_run_id: int,
    expected_head_sha: str,
) -> ProviderJob | None:
    job = _object(value)
    if job is None or "conclusion" not in job:
        return None
    job_id = _positive_safe_integer(job.get("id"))
    run_id = _positive_safe_integer(job.get("run_id"))
    name = _bounded_string(job.get("name"), MAX_JOB_NAME_CODE_POINTS)
    head_sha = _sha1(job.get("head_sha"))
    state = _decode_job_state(job.get("status"), job.get("conclusion"))
    started_at = _timestamp_or_none(job.get("started_at"))
    completed_at = _timestamp_or_none(job.get("completed_at"))
    labels = _labels(job.get("labels"))
    runner = _runner(job)
    if (
        job_id is None
        or run_id != expected_run_id
        or name is None
        or head_sha != expected_head_sha
        or state is None
        or started_at is _INVALID
        or completed_at is _INVALID
        or labels is None
        or runner is None
    ):
        return None
    admitted_started_at = cast(datetime | None, started_at)
    admitted_completed_at = cast(datetime | None, completed_at)
    return ProviderJob(
        job_id=job_id,
        name=name,
        status=state[0],
        conclusion=state[1],
        head_sha=head_sha,
        started_at=admitted_started_at,
        completed_at=admitted_completed_at,
        labels=labels,
        runner=runner,
    )


def _decode_job_state(
    raw_status: object,
    raw_conclusion: object,
) -> tuple[SignalStatus, WorkflowConclusion | None] | None:
    status = _non_empty_string(raw_status)
    if status in _ACTIVE_JOB_STATUSES:
        return ("in_progress", None) if raw_conclusion is None else None
    if status != "completed":
        return None
    conclusion = _non_empty_string(raw_conclusion)
    if conclusion in _DIRECT_CONCLUSIONS:
        return "completed", cast(WorkflowConclusion, conclusion)
    if conclusion in _UNKNOWN_FAILURE_CONCLUSIONS:
        return "completed", cast(WorkflowConclusion, conclusion)
    return None


def _non_empty_string(value: object) -> str | None:
    if not _unicode_scalar_string(value) or not value:
        return None
    return cast(str, value)


_INVALID = object()


def _bounded_string(value: object, maximum_code_points: int) -> str | None:
    text = _non_empty_string(value)
    if text is None or len(text) > maximum_code_points:
        return None
    return text


def _sha1(value: object) -> str | None:
    text = _non_empty_string(value)
    if (
        text is None
        or len(text) != 40
        or any(character not in "0123456789abcdef" for character in text)
    ):
        return None
    return text


def _timestamp_or_none(value: object) -> datetime | object | None:
    if value is None:
        return None
    text = _non_empty_string(value)
    if text is None or not text.endswith("Z"):
        return _INVALID
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError:
        return _INVALID
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return _INVALID
    return parsed.astimezone(UTC)


def _labels(value: object) -> tuple[str, ...] | None:
    if type(value) is not list or len(value) > MAX_JOB_LABELS:
        return None
    labels: list[str] = []
    for item in value:
        label = _bounded_string(item, MAX_JOB_LABEL_CODE_POINTS)
        if label is None:
            return None
        labels.append(label)
    if len(labels) != len(set(labels)):
        return None
    return tuple(sorted(labels))


def _runner(job: dict[str, object]) -> RunnerIdentity | None:
    runner_id = _optional_runner_id(job.get("runner_id"))
    runner_group_id = _optional_runner_id(job.get("runner_group_id"))
    runner_name = _optional_bounded_string(job.get("runner_name"))
    runner_group_name = _optional_bounded_string(job.get("runner_group_name"))
    if _INVALID in {runner_id, runner_group_id, runner_name, runner_group_name}:
        return None
    if runner_id == 0:
        runner_id, runner_name = None, None
    if runner_group_id == 0:
        runner_group_id, runner_group_name = None, None
    try:
        return RunnerIdentity(
            runner_id=cast(int | None, runner_id),
            runner_name=cast(str | None, runner_name),
            runner_group_id=cast(int | None, runner_group_id),
            runner_group_name=cast(str | None, runner_group_name),
        )
    except (TypeError, ValueError):
        return None


def _optional_runner_id(value: object) -> int | object | None:
    if value is None:
        return None
    parsed = _non_negative_safe_integer(value)
    return parsed if parsed is not None else _INVALID


def _optional_bounded_string(value: object) -> str | object | None:
    if value is None or (type(value) is str and value == ""):
        return None
    parsed = _bounded_string(value, MAX_RUNNER_TEXT_CODE_POINTS)
    return parsed if parsed is not None else _INVALID


def _unicode_scalar_string(value: object) -> bool:
    return type(value) is str and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
