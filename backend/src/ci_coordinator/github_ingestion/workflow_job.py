from __future__ import annotations

from datetime import datetime
from typing import cast

from ci_coordinator.github_ingestion.event_common import (
    EventCommon,
    git_sha,
    object_field,
    positive_integer,
)
from ci_coordinator.github_ingestion.event_common import timestamp as _timestamp
from ci_coordinator.github_ingestion.events import (
    NormalizedWorkflowJobEvent,
    WorkflowJobConclusion,
    WorkflowJobRunnerIdentity,
)
from ci_coordinator.github_ingestion.results import UnsupportedWebhook
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject

_MAXIMUM_JOB_NAME_CODE_POINTS = 512
_MAXIMUM_LABEL_COUNT = 32
_MAXIMUM_LABEL_CODE_POINTS = 128
_MAXIMUM_RUNNER_TEXT_CODE_POINTS = 256


def normalize_workflow_job(
    payload: FrozenJsonObject,
    common: EventCommon,
) -> NormalizedWorkflowJobEvent | UnsupportedWebhook:
    workflow_job = object_field(payload, "workflow_job")
    if common.action != "completed" or workflow_job is None:
        return _incomplete(common)

    workflow_run_id = positive_integer(workflow_job.get("run_id"))
    run_attempt = positive_integer(workflow_job.get("run_attempt"))
    head_sha = git_sha(workflow_job, "head_sha")
    workflow_job_id = positive_integer(workflow_job.get("id"))
    job_name = _bounded_text(workflow_job.get("name"), _MAXIMUM_JOB_NAME_CODE_POINTS)
    conclusion = _conclusion(workflow_job.get("conclusion"))
    timestamps = _timestamps(workflow_job)
    labels = _labels(workflow_job.get("labels"))
    runner_valid, runner = _runner_identity(workflow_job)
    if (
        workflow_run_id is None
        or run_attempt is None
        or head_sha is None
        or workflow_job_id is None
        or job_name is None
        or workflow_job.get("status") != "completed"
        or conclusion is None
        or timestamps is None
        or labels is None
        or not runner_valid
    ):
        return _incomplete(common)

    created_at, started_at, completed_at = timestamps
    return NormalizedWorkflowJobEvent(
        provenance=common.provenance,
        repository=common.repository,
        action=common.action,
        workflow_run_id=workflow_run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
        workflow_job_id=workflow_job_id,
        job_name=job_name,
        status="completed",
        conclusion=conclusion,
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        labels=labels,
        runner=runner,
    )


def _timestamps(
    workflow_job: FrozenJsonObject,
) -> tuple[datetime, datetime, datetime] | None:
    created_at = _timestamp(workflow_job.get("created_at"))
    started_at = _timestamp(workflow_job.get("started_at"))
    completed_at = _timestamp(workflow_job.get("completed_at"))
    if (
        created_at is None
        or started_at is None
        or completed_at is None
        or not created_at <= started_at <= completed_at
    ):
        return None
    return created_at, started_at, completed_at


def _labels(value: object) -> tuple[str, ...] | None:
    if type(value) is not tuple or len(value) > _MAXIMUM_LABEL_COUNT:
        return None
    labels = tuple(_bounded_text(label, _MAXIMUM_LABEL_CODE_POINTS) for label in value)
    if any(label is None for label in labels):
        return None
    admitted = cast(tuple[str, ...], labels)
    return tuple(sorted(set(admitted))) if len(set(admitted)) == len(admitted) else None


def _runner_identity(
    workflow_job: FrozenJsonObject,
) -> tuple[bool, WorkflowJobRunnerIdentity | None]:
    runner_id_valid, runner_id = _nullable_runner_id(workflow_job.get("runner_id"))
    runner_name_valid, runner_name = _nullable_bounded_text(
        workflow_job.get("runner_name"),
        _MAXIMUM_RUNNER_TEXT_CODE_POINTS,
    )
    group_id_valid, group_id = _nullable_runner_id(workflow_job.get("runner_group_id"))
    group_name_valid, group_name = _nullable_bounded_text(
        workflow_job.get("runner_group_name"),
        _MAXIMUM_RUNNER_TEXT_CODE_POINTS,
    )
    if not all((runner_id_valid, runner_name_valid, group_id_valid, group_name_valid)):
        return False, None
    if runner_id == 0:
        runner_id, runner_name = None, None
    if group_id == 0:
        group_id, group_name = None, None
    if (runner_id is None) != (runner_name is None):
        return False, None
    if (group_id is None) != (group_name is None):
        return False, None
    if runner_id is None:
        return (group_id is None), None
    return True, WorkflowJobRunnerIdentity(
        runner_id=runner_id,
        runner_name=cast(str, runner_name),
        runner_group_id=group_id,
        runner_group_name=group_name,
    )


def _nullable_runner_id(value: object) -> tuple[bool, int | None]:
    if value is None:
        return True, None
    if type(value) is int and value == 0:
        return True, 0
    admitted = positive_integer(value)
    return (admitted is not None), admitted


def _nullable_bounded_text(
    value: object,
    maximum_code_points: int,
) -> tuple[bool, str | None]:
    if value is None or (type(value) is str and value == ""):
        return True, None
    admitted = _bounded_text(value, maximum_code_points)
    return (admitted is not None), admitted


def _bounded_text(value: object, maximum_code_points: int) -> str | None:
    if type(value) is not str or not value:
        return None
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return None
    return value if len(value) <= maximum_code_points else None


def _conclusion(value: object) -> WorkflowJobConclusion | None:
    if value in {
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
        return value
    return None


def _incomplete(common: EventCommon) -> UnsupportedWebhook:
    return UnsupportedWebhook(
        provenance=common.provenance,
        repository=common.repository,
        action=common.action,
        reason_code="incomplete_workflow_job",
    )
