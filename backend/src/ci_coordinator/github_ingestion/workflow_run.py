from __future__ import annotations

from datetime import datetime

from ci_coordinator.github_ingestion.event_common import (
    EventCommon,
    git_sha,
    object_field,
    optional_string,
    positive_integer,
)
from ci_coordinator.github_ingestion.event_common import timestamp as _timestamp
from ci_coordinator.github_ingestion.events import (
    NormalizedWorkflowRunEvent,
    WorkflowRunConclusion,
    WorkflowRunStatus,
)
from ci_coordinator.github_ingestion.results import UnsupportedWebhook
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject


def normalize_workflow_run(
    payload: FrozenJsonObject,
    common: EventCommon,
) -> NormalizedWorkflowRunEvent | UnsupportedWebhook:
    workflow_run = object_field(payload, "workflow_run")
    workflow_run_id = positive_integer(workflow_run.get("id")) if workflow_run else None
    workflow_name = optional_string(workflow_run, "name")
    workflow_file = optional_string(workflow_run, "path")
    head_sha = git_sha(workflow_run, "head_sha")
    run_attempt = positive_integer(workflow_run.get("run_attempt")) if workflow_run else None
    timestamps = _timestamps(workflow_run)
    if (
        workflow_run_id is None
        or run_attempt is None
        or workflow_name is None
        or workflow_file is None
        or head_sha is None
    ):
        return UnsupportedWebhook(
            provenance=common.provenance,
            repository=common.repository,
            action=common.action,
            reason_code="incomplete_workflow_run",
        )
    status = _status(optional_string(workflow_run, "status"))
    conclusion = _conclusion(optional_string(workflow_run, "conclusion"))
    if common.action == "completed" and (
        status != "completed"
        or conclusion is None
        or timestamps is None
        or any(value is None for value in timestamps)
    ):
        return UnsupportedWebhook(
            provenance=common.provenance,
            repository=common.repository,
            action=common.action,
            reason_code="incomplete_workflow_run",
        )
    created_at, run_started_at, updated_at = timestamps or (None, None, None)
    return NormalizedWorkflowRunEvent(
        provenance=common.provenance,
        repository=common.repository,
        action=common.action,
        workflow_run_id=workflow_run_id,
        run_attempt=run_attempt,
        workflow_id=positive_integer(workflow_run.get("workflow_id")) if workflow_run else None,
        workflow_file=workflow_file,
        workflow_name=workflow_name,
        head_branch=optional_string(workflow_run, "head_branch"),
        head_sha=head_sha,
        workflow_event=optional_string(workflow_run, "event") or "unknown",
        status=status,
        conclusion=conclusion,
        created_at=created_at,
        run_started_at=run_started_at,
        updated_at=updated_at,
    )


def _timestamps(
    workflow_run: FrozenJsonObject | None,
) -> tuple[datetime | None, datetime | None, datetime | None] | None:
    if workflow_run is None:
        return None
    created_at = _timestamp(workflow_run.get("created_at"))
    run_started_at = _timestamp(workflow_run.get("run_started_at"))
    updated_at = _timestamp(workflow_run.get("updated_at"))
    if created_at is None and run_started_at is None and updated_at is None:
        return None
    known = tuple(item for item in (created_at, run_started_at, updated_at) if item is not None)
    if tuple(sorted(known)) != known:
        return None
    return created_at, run_started_at, updated_at


def _status(value: str | None) -> WorkflowRunStatus:
    match value:
        case "requested" | "queued" | "in_progress" | "waiting" | "completed":
            return value
        case _:
            return "unknown"


def _conclusion(value: str | None) -> WorkflowRunConclusion | None:
    match value:
        case None:
            return None
        case (
            "success"
            | "failure"
            | "cancelled"
            | "timed_out"
            | "skipped"
            | "neutral"
            | "action_required"
            | "startup_failure"
            | "stale"
        ):
            return value
        case _:
            return None
