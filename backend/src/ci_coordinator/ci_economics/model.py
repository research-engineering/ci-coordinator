"""Exact CI evidence identities and bounded read values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Final, Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

MAX_ECONOMICS_PAGE_SIZE: Final = 100
MAX_JOBS_PER_ATTEMPT: Final = 2_000
MAX_JOB_NAME_CODE_POINTS: Final = 512
MAX_RUNNER_TEXT_CODE_POINTS: Final = 256
MAX_JOB_LABELS: Final = 32
MAX_JOB_LABEL_CODE_POINTS: Final = 128
MEASUREMENT_DEFINITION_VERSION: Final = "ci-economics-measurement/v1"

type PlannedRoute = Literal["selected", "full_ci_counterfactual", "unknown"]
type EvidenceQuality = Literal["exact", "partial", "unknown", "conflict"]
type WorkflowConclusion = Literal[
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
    "neutral",
    "action_required",
    "startup_failure",
    "stale",
]


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    scope: RepositoryScope
    workflow_run_id: int
    run_attempt: int
    head_sha: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("attempt identity requires an exact repository scope")
        for name, value in (
            ("workflow_run_id", self.workflow_run_id),
            ("run_attempt", self.run_attempt),
        ):
            if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
                raise ValueError(f"{name} must be a positive JSON-safe integer")
        _require_sha(self.head_sha, "head_sha", length=40)

    @property
    def identity_hash(self) -> str:
        return hash_object(self.canonical_mapping())

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "workflowRunId": self.workflow_run_id,
            "runAttempt": self.run_attempt,
            "headSha": self.head_sha,
        }


@dataclass(frozen=True, slots=True)
class RunnerIdentity:
    runner_id: int | None
    runner_name: str | None
    runner_group_id: int | None
    runner_group_name: str | None

    def __post_init__(self) -> None:
        for name, numeric_value in (
            ("runner_id", self.runner_id),
            ("runner_group_id", self.runner_group_id),
        ):
            if numeric_value is not None and (
                type(numeric_value) is not int or not 1 <= numeric_value <= MAX_SAFE_JSON_INTEGER
            ):
                raise ValueError(f"{name} must be a positive JSON-safe integer or absent")
        for name, text_value in (
            ("runner_name", self.runner_name),
            ("runner_group_name", self.runner_group_name),
        ):
            if text_value is not None:
                _require_bounded_text(text_value, name, MAX_RUNNER_TEXT_CODE_POINTS)
        if (self.runner_id is None) != (self.runner_name is None):
            raise ValueError("runner id and name must either both be present or both be absent")
        if (self.runner_group_id is None) != (self.runner_group_name is None):
            raise ValueError(
                "runner group id and name must either both be present or both be absent"
            )
        if self.runner_id is None and self.runner_group_id is not None:
            raise ValueError("runner group requires a runner identity")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "runnerId": self.runner_id,
            "runnerName": self.runner_name,
            "runnerGroupId": self.runner_group_id,
            "runnerGroupName": self.runner_group_name,
        }


@dataclass(frozen=True, slots=True)
class JobTiming:
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None

    def __post_init__(self) -> None:
        instants = tuple(
            _normalize_instant(value, name)
            for name, value in (
                ("created_at", self.created_at),
                ("started_at", self.started_at),
                ("completed_at", self.completed_at),
            )
        )
        object.__setattr__(self, "created_at", instants[0])
        object.__setattr__(self, "started_at", instants[1])
        object.__setattr__(self, "completed_at", instants[2])
        known = tuple(value for value in instants if value is not None)
        if tuple(sorted(known)) != known:
            raise ValueError("known job timestamps must be monotonic")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "createdAt": _format_instant(self.created_at),
            "startedAt": _format_instant(self.started_at),
            "completedAt": _format_instant(self.completed_at),
        }


@dataclass(frozen=True, slots=True)
class WorkflowJobFact:
    attempt: AttemptIdentity
    provider_job_id: int
    name: str
    conclusion: WorkflowConclusion
    timing: JobTiming
    labels: tuple[str, ...]
    runner: RunnerIdentity
    semantic_hash: str
    delivery_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("workflow job fact requires an exact attempt identity")
        if (
            type(self.provider_job_id) is not int
            or not 1 <= self.provider_job_id <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("provider job id must be a positive JSON-safe integer")
        _require_bounded_text(self.name, "job name", MAX_JOB_NAME_CODE_POINTS)
        if type(self.conclusion) is not str or self.conclusion not in {
            "success",
            "failure",
            "cancelled",
            "timed_out",
            "skipped",
            "neutral",
            "action_required",
            "startup_failure",
            "stale",
        }:
            raise ValueError("workflow job conclusion is invalid")
        if type(self.timing) is not JobTiming:
            raise TypeError("workflow job fact requires exact timing")
        _require_labels(self.labels)
        if type(self.runner) is not RunnerIdentity:
            raise TypeError("workflow job fact requires an exact runner identity")
        _require_sha(self.semantic_hash, "semantic_hash", length=64)
        if self.semantic_hash != hash_object(self.canonical_mapping()):
            raise ValueError("workflow job semantic hash does not match its canonical facts")
        if self.delivery_id is not None:
            _require_bounded_text(self.delivery_id, "delivery_id", 128)

    @property
    def natural_identity_hash(self) -> str:
        return hash_object(
            {
                "attempt": self.attempt.canonical_mapping(),
                "providerJobId": self.provider_job_id,
            }
        )

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "attempt": self.attempt.canonical_mapping(),
            "providerJobId": self.provider_job_id,
            "name": self.name,
            "conclusion": self.conclusion,
            "timing": self.timing.canonical_mapping(),
            "labels": list(self.labels),
            "runner": self.runner.canonical_mapping(),
        }


@dataclass(frozen=True, slots=True)
class AttemptSnapshot:
    subject_id: str
    attempt: AttemptIdentity
    contract_hash: str
    planned_route: PlannedRoute
    recorded_at: datetime
    retain_until: datetime
    snapshot_digest: str
    jobs: tuple[WorkflowJobFact, ...]

    def __post_init__(self) -> None:
        _require_sha(self.subject_id, "subject_id", length=64)
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("attempt snapshot requires an exact attempt identity")
        _require_sha(self.contract_hash, "contract_hash", length=64)
        if type(self.planned_route) is not str or self.planned_route not in {
            "selected",
            "full_ci_counterfactual",
            "unknown",
        }:
            raise ValueError("planned route is invalid")
        normalized = _normalize_instant(self.recorded_at, "recorded_at")
        if normalized is None:
            raise ValueError("snapshot recording time is required")
        object.__setattr__(self, "recorded_at", normalized)
        retained = _normalize_instant(self.retain_until, "retain_until")
        if retained is None or retained <= normalized:
            raise ValueError("snapshot retention must follow its recording time")
        object.__setattr__(self, "retain_until", retained)
        _require_sha(self.snapshot_digest, "snapshot_digest", length=64)
        if type(self.jobs) is not tuple or any(
            type(job) is not WorkflowJobFact for job in self.jobs
        ):
            raise TypeError("attempt snapshot jobs must be an exact tuple of facts")
        if not 1 <= len(self.jobs) <= MAX_JOBS_PER_ATTEMPT:
            raise ValueError("attempt snapshot job count is outside its admitted bound")
        job_ids = tuple(job.provider_job_id for job in self.jobs)
        if any(left >= right for left, right in pairwise(job_ids)):
            raise ValueError("attempt snapshot jobs must be strictly ordered by provider job id")
        if any(job.attempt != self.attempt or job.delivery_id is not None for job in self.jobs):
            raise ValueError("attempt snapshot jobs must be provider-only facts for one attempt")
        if self.snapshot_digest != _snapshot_digest(self.attempt, self.jobs):
            raise ValueError("attempt snapshot digest does not match its canonical jobs")


@dataclass(frozen=True, slots=True)
class ProviderAttemptSnapshot:
    """Stable provider evidence before durable contract correlation."""

    subject_id: str
    attempt: AttemptIdentity
    snapshot_digest: str
    jobs: tuple[WorkflowJobFact, ...]

    def __post_init__(self) -> None:
        _require_sha(self.subject_id, "subject_id", length=64)
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("provider snapshot requires an exact attempt identity")
        _require_sha(self.snapshot_digest, "snapshot_digest", length=64)
        if type(self.jobs) is not tuple or any(
            type(job) is not WorkflowJobFact for job in self.jobs
        ):
            raise TypeError("provider snapshot jobs must be an exact tuple of facts")
        if not 1 <= len(self.jobs) <= MAX_JOBS_PER_ATTEMPT:
            raise ValueError("provider snapshot job count is outside its admitted bound")
        job_ids = tuple(job.provider_job_id for job in self.jobs)
        if any(left >= right for left, right in pairwise(job_ids)):
            raise ValueError("provider snapshot jobs must be strictly ordered by provider job id")
        if any(job.attempt != self.attempt or job.delivery_id is not None for job in self.jobs):
            raise ValueError("provider snapshot jobs must be provider-only facts for one attempt")
        if self.snapshot_digest != _snapshot_digest(self.attempt, self.jobs):
            raise ValueError("provider snapshot digest does not match its canonical jobs")


@dataclass(frozen=True, slots=True)
class DurationAggregate:
    quality: EvidenceQuality
    known_value_ms: int | None
    known_job_count: int
    total_job_count: int
    reason_code: str | None

    def __post_init__(self) -> None:
        if type(self.quality) is not str or self.quality not in {
            "exact",
            "partial",
            "unknown",
            "conflict",
        }:
            raise ValueError("duration quality is invalid")
        if self.known_value_ms is not None and (
            type(self.known_value_ms) is not int
            or not 0 <= self.known_value_ms <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("known duration must be JSON-safe milliseconds or absent")
        if (
            type(self.known_job_count) is not int
            or type(self.total_job_count) is not int
            or not 0 <= self.known_job_count <= self.total_job_count <= MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("duration job coverage is invalid")
        if self.quality == "exact" and (
            self.known_value_ms is None or self.known_job_count != self.total_job_count
        ):
            raise ValueError("exact duration requires complete known coverage")
        if self.quality in {"unknown", "conflict"} and self.known_value_ms is not None:
            raise ValueError("unknown or conflicting duration cannot expose a value")
        if self.quality == "partial" and (
            self.known_value_ms is None
            or self.known_job_count == 0
            or self.known_job_count >= self.total_job_count
        ):
            raise ValueError("partial duration requires strict non-empty partial coverage")
        if self.quality == "exact" and self.reason_code is not None:
            raise ValueError("exact duration cannot have an unavailable reason")
        if self.quality != "exact":
            _require_bounded_text(self.reason_code, "reason_code", 128)


@dataclass(frozen=True, slots=True)
class AttemptMeasurements:
    definition_version: str
    queue: DurationAggregate
    runner_occupancy: DurationAggregate
    attempt_wall: DurationAggregate
    observation_set_hash: str

    def __post_init__(self) -> None:
        if self.definition_version != MEASUREMENT_DEFINITION_VERSION:
            raise ValueError("measurement definition version is unsupported")
        for value in (self.queue, self.runner_occupancy, self.attempt_wall):
            if type(value) is not DurationAggregate:
                raise TypeError("attempt economics durations must be exact aggregates")
        if not 1 <= self.queue.total_job_count <= MAX_JOBS_PER_ATTEMPT or any(
            value.total_job_count != self.queue.total_job_count
            for value in (self.queue, self.runner_occupancy, self.attempt_wall)
        ):
            raise ValueError("attempt economics coverage crosses snapshot cardinality")
        conflict_count = sum(
            value.quality == "conflict"
            for value in (self.queue, self.runner_occupancy, self.attempt_wall)
        )
        if conflict_count not in {0, 3}:
            raise ValueError("attempt economics conflict must invalidate every aggregate")
        _require_sha(self.observation_set_hash, "observation_set_hash", length=64)


@dataclass(frozen=True, slots=True)
class AttemptEconomics:
    snapshot: AttemptSnapshot
    definition_version: str
    queue: DurationAggregate
    runner_occupancy: DurationAggregate
    attempt_wall: DurationAggregate
    observation_set_hash: str

    def __post_init__(self) -> None:
        if type(self.snapshot) is not AttemptSnapshot:
            raise TypeError("attempt economics requires an exact snapshot")
        AttemptMeasurements(
            self.definition_version,
            self.queue,
            self.runner_occupancy,
            self.attempt_wall,
            self.observation_set_hash,
        )
        if self.queue.total_job_count != len(self.snapshot.jobs):
            raise ValueError("attempt economics coverage crosses snapshot cardinality")


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    subject_id: str
    attempt: AttemptIdentity
    contract_hash: str
    planned_route: PlannedRoute
    job_count: int
    snapshot_digest: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        _require_sha(self.subject_id, "subject_id", length=64)
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("attempt summary requires an exact attempt identity")
        _require_sha(self.contract_hash, "contract_hash", length=64)
        if type(self.planned_route) is not str or self.planned_route not in {
            "selected",
            "full_ci_counterfactual",
            "unknown",
        }:
            raise ValueError("attempt summary planned route is invalid")
        if type(self.job_count) is not int or not 1 <= self.job_count <= MAX_JOBS_PER_ATTEMPT:
            raise ValueError("attempt summary job count is outside its admitted bound")
        _require_sha(self.snapshot_digest, "snapshot_digest", length=64)
        recorded_at = _normalize_instant(self.recorded_at, "recorded_at")
        if recorded_at is None:
            raise ValueError("attempt summary recorded_at is required")
        object.__setattr__(self, "recorded_at", recorded_at)

    @property
    def cursor(self) -> str:
        return encode_attempt_cursor(self.recorded_at, self.subject_id)


@dataclass(frozen=True, slots=True)
class AttemptSummaryPage:
    items: tuple[AttemptSummary, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            type(item) is not AttemptSummary for item in self.items
        ):
            raise TypeError("attempt summary page requires exact items")
        if not 0 <= len(self.items) <= MAX_ECONOMICS_PAGE_SIZE:
            raise ValueError("attempt summary page exceeds its cardinality bound")
        ordering = tuple((item.recorded_at, item.subject_id) for item in self.items)
        if any(left <= right for left, right in pairwise(ordering)):
            raise ValueError("attempt summary page is not strictly newest-first")
        if self.next_cursor is not None and (
            not self.items or self.next_cursor != self.items[-1].cursor
        ):
            raise ValueError("attempt summary cursor does not identify its final item")


def encode_attempt_cursor(recorded_at: datetime, subject_id: str) -> str:
    normalized = _normalize_instant(recorded_at, "attempt cursor time")
    if normalized is None:
        raise ValueError("attempt cursor time is required")
    _require_sha(subject_id, "attempt cursor subject", length=64)
    return f"{_format_instant(normalized)}.{subject_id}"


def decode_attempt_cursor(value: str) -> tuple[datetime, str]:
    if type(value) is not str or len(value) != 92 or value[27] != ".":
        raise ValueError("attempt cursor is invalid")
    timestamp = value[:27]
    subject_id = value[28:]
    _require_sha(subject_id, "attempt cursor subject", length=64)
    try:
        parsed = datetime.fromisoformat(f"{timestamp[:-1]}+00:00")
    except ValueError:
        raise ValueError("attempt cursor time is invalid") from None
    if not timestamp.endswith("Z") or _format_instant(parsed) != timestamp:
        raise ValueError("attempt cursor time is not canonical")
    return parsed.astimezone(UTC), subject_id


def _require_sha(value: object, name: str, *, length: int) -> None:
    if type(value) is not str or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise ValueError(f"{name} must be lowercase hexadecimal with length {length}")


def _require_bounded_text(value: object, name: str, maximum: int) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError(f"{name} must be bounded non-empty Unicode scalar text")


def _require_labels(labels: object) -> None:
    if type(labels) is not tuple or not 0 <= len(labels) <= MAX_JOB_LABELS:
        raise ValueError("job labels exceed their cardinality bound")
    if any(
        type(label) is not str
        or not label
        or len(label) > MAX_JOB_LABEL_CODE_POINTS
        or any(0xD800 <= ord(character) <= 0xDFFF for character in label)
        for label in labels
    ):
        raise ValueError("job labels contain invalid text")
    if labels != tuple(sorted(set(labels))):
        raise ValueError("job labels must be sorted and unique")


def _normalize_instant(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware or absent")
    return value.astimezone(UTC)


def _format_instant(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_digest(
    attempt: AttemptIdentity,
    jobs: tuple[WorkflowJobFact, ...],
) -> str:
    return hash_object(
        {
            "attempt": attempt.canonical_mapping(),
            "jobs": [job.canonical_mapping() for job in jobs],
        }
    )
