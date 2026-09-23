from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance

type WorkflowRunStatus = Literal[
    "requested",
    "queued",
    "in_progress",
    "waiting",
    "completed",
    "unknown",
]
type WorkflowRunConclusion = Literal[
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
type WorkflowJobConclusion = Literal[
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
class NormalizedEvent:
    provenance: WebhookProvenance
    repository: GitHubRepository
    action: str | None


@dataclass(frozen=True, slots=True)
class NormalizedPushEvent(NormalizedEvent):
    ref: str
    base_sha: str
    head_sha: str
    deleted: bool
    kind: Literal["push"] = field(default="push", init=False)


@dataclass(frozen=True, slots=True)
class NormalizedPullRequestEvent(NormalizedEvent):
    pull_request_number: int
    base_sha: str
    head_sha: str
    kind: Literal["pull_request"] = field(default="pull_request", init=False)


@dataclass(frozen=True, slots=True)
class NormalizedMergeGroupEvent(NormalizedEvent):
    base_sha: str
    head_sha: str
    head_ref: str | None
    kind: Literal["merge_group"] = field(default="merge_group", init=False)


@dataclass(frozen=True, slots=True)
class NormalizedWorkflowRunEvent(NormalizedEvent):
    workflow_run_id: int
    run_attempt: int
    workflow_id: int | None
    workflow_file: str
    workflow_name: str
    head_branch: str | None
    head_sha: str
    workflow_event: str
    status: WorkflowRunStatus
    conclusion: WorkflowRunConclusion | None
    created_at: datetime | None
    run_started_at: datetime | None
    updated_at: datetime | None
    kind: Literal["workflow_run"] = field(default="workflow_run", init=False)


@dataclass(frozen=True, slots=True)
class WorkflowJobRunnerIdentity:
    runner_id: int
    runner_name: str
    runner_group_id: int | None
    runner_group_name: str | None


@dataclass(frozen=True, slots=True)
class NormalizedWorkflowJobEvent(NormalizedEvent):
    workflow_run_id: int
    run_attempt: int
    head_sha: str
    workflow_job_id: int
    job_name: str
    status: Literal["completed"]
    conclusion: WorkflowJobConclusion
    created_at: datetime
    started_at: datetime
    completed_at: datetime
    labels: tuple[str, ...]
    runner: WorkflowJobRunnerIdentity | None
    kind: Literal["workflow_job"] = field(default="workflow_job", init=False)


type NormalizedGitHubEvent = (
    NormalizedPushEvent
    | NormalizedPullRequestEvent
    | NormalizedMergeGroupEvent
    | NormalizedWorkflowRunEvent
    | NormalizedWorkflowJobEvent
)

type DurableWorkflowObservation = NormalizedWorkflowRunEvent | NormalizedWorkflowJobEvent
