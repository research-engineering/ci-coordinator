"""Canonical codecs for retained CI economics provider evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from ci_coordinator.ci_economics import (
    AttemptIdentity,
    JobTiming,
    RunnerIdentity,
    WorkflowConclusion,
    WorkflowJobFact,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.github_ingestion.events import (
    DurableWorkflowObservation,
    NormalizedWorkflowJobEvent,
    NormalizedWorkflowRunEvent,
)
from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
    require_digest,
    require_exact_keys,
)

_MAX_OBSERVATION_CANONICAL_BYTES = 16_384


class CiEconomicsCodecError(ValueError):
    """Retained CI economics evidence is outside the admitted domain."""


def encode_workflow_observation(
    event: DurableWorkflowObservation,
    *,
    delivery_id: str,
) -> dict[str, object]:
    if type(event) is NormalizedWorkflowJobEvent:
        mapping = _job_event_mapping(event)
        provider_job_id: int | None = event.workflow_job_id
    elif type(event) is NormalizedWorkflowRunEvent and event.action == "completed":
        mapping = _run_event_mapping(event)
        provider_job_id = None
    else:
        raise CiEconomicsCodecError("only completed workflow observations are durable")
    canonical = encode_canonical_object(
        mapping,
        maximum_bytes=_MAX_OBSERVATION_CANONICAL_BYTES,
        context="CI workflow observation",
    )
    semantic_mapping = (
        _job_fact_mapping(event) if type(event) is NormalizedWorkflowJobEvent else mapping
    )
    return {
        "delivery_id": delivery_id,
        "observation_kind": event.kind,
        "installation_id": event.repository.installation_id,
        "repository_id": event.repository.repository_id,
        "workflow_run_id": event.workflow_run_id,
        "run_attempt": event.run_attempt,
        "head_sha": event.head_sha,
        "provider_job_id": provider_job_id,
        "semantic_hash": hash_object(semantic_mapping),
        "observation_canonical_json": canonical,
    }


def decode_workflow_job_observation(row: dict[str, object]) -> WorkflowJobFact:
    if row.get("observation_kind") != "workflow_job":
        raise CiEconomicsCodecError("stored observation is not a workflow job")
    mapping = decode_canonical_object(
        row.get("observation_canonical_json"),
        maximum_bytes=_MAX_OBSERVATION_CANONICAL_BYTES,
        context="CI workflow observation",
    )
    require_exact_keys(
        mapping,
        {
            "schemaVersion",
            "installationId",
            "repositoryId",
            "workflowRunId",
            "runAttempt",
            "headSha",
            "providerJobId",
            "name",
            "status",
            "conclusion",
            "createdAt",
            "startedAt",
            "completedAt",
            "labels",
            "runner",
        },
        "CI workflow job observation",
    )
    if (
        mapping["schemaVersion"] != "ci-workflow-job-observation/v1"
        or mapping["status"] != "completed"
    ):
        raise CiEconomicsCodecError("stored workflow job observation identity is invalid")
    semantic_hash = require_digest(row.get("semantic_hash"), "stored observation semantic hash")
    try:
        attempt = AttemptIdentity(
            RepositoryScope(
                _positive(mapping["installationId"], "installationId"),
                _positive(mapping["repositoryId"], "repositoryId"),
            ),
            _positive(mapping["workflowRunId"], "workflowRunId"),
            _positive(mapping["runAttempt"], "runAttempt"),
            _text(mapping["headSha"], "headSha"),
        )
        fact = WorkflowJobFact(
            attempt=attempt,
            provider_job_id=_positive(mapping["providerJobId"], "providerJobId"),
            name=_text(mapping["name"], "name"),
            conclusion=cast(WorkflowConclusion, mapping["conclusion"]),
            timing=JobTiming(
                _instant(mapping["createdAt"], "createdAt"),
                _instant(mapping["startedAt"], "startedAt"),
                _instant(mapping["completedAt"], "completedAt"),
            ),
            labels=_text_tuple(mapping["labels"], "labels"),
            runner=_runner(mapping["runner"]),
            semantic_hash=semantic_hash,
            delivery_id=_text(row.get("delivery_id"), "delivery_id"),
        )
    except (TypeError, ValueError) as error:
        raise CiEconomicsCodecError("stored workflow job observation is invalid") from error
    expected_columns = (
        fact.attempt.scope.installation_id,
        fact.attempt.scope.repository_id,
        fact.attempt.workflow_run_id,
        fact.attempt.run_attempt,
        fact.attempt.head_sha,
        fact.provider_job_id,
    )
    observed_columns = (
        _positive(row.get("installation_id"), "installation_id"),
        _positive(row.get("repository_id"), "repository_id"),
        _positive(row.get("workflow_run_id"), "workflow_run_id"),
        _positive(row.get("run_attempt"), "run_attempt"),
        _text(row.get("head_sha"), "head_sha"),
        _positive(row.get("provider_job_id"), "provider_job_id"),
    )
    if observed_columns != expected_columns:
        raise CiEconomicsCodecError("stored workflow job columns diverge from canonical evidence")
    return fact


def _job_event_mapping(event: NormalizedWorkflowJobEvent) -> dict[str, object]:
    return {
        "schemaVersion": "ci-workflow-job-observation/v1",
        "installationId": event.repository.installation_id,
        "repositoryId": event.repository.repository_id,
        "workflowRunId": event.workflow_run_id,
        "runAttempt": event.run_attempt,
        "headSha": event.head_sha,
        "providerJobId": event.workflow_job_id,
        "name": event.job_name,
        "status": event.status,
        "conclusion": event.conclusion,
        "createdAt": _format_instant(event.created_at),
        "startedAt": _format_instant(event.started_at),
        "completedAt": _format_instant(event.completed_at),
        "labels": list(event.labels),
        "runner": (
            None
            if event.runner is None
            else {
                "runnerId": event.runner.runner_id,
                "runnerName": event.runner.runner_name,
                "runnerGroupId": event.runner.runner_group_id,
                "runnerGroupName": event.runner.runner_group_name,
            }
        ),
    }


def _job_fact_mapping(event: NormalizedWorkflowJobEvent) -> dict[str, object]:
    return {
        "attempt": {
            "installationId": event.repository.installation_id,
            "repositoryId": event.repository.repository_id,
            "workflowRunId": event.workflow_run_id,
            "runAttempt": event.run_attempt,
            "headSha": event.head_sha,
        },
        "providerJobId": event.workflow_job_id,
        "name": event.job_name,
        "conclusion": event.conclusion,
        "timing": {
            "createdAt": _format_instant(event.created_at),
            "startedAt": _format_instant(event.started_at),
            "completedAt": _format_instant(event.completed_at),
        },
        "labels": list(event.labels),
        "runner": (
            RunnerIdentity(None, None, None, None).canonical_mapping()
            if event.runner is None
            else RunnerIdentity(
                event.runner.runner_id,
                event.runner.runner_name,
                event.runner.runner_group_id,
                event.runner.runner_group_name,
            ).canonical_mapping()
        ),
    }


def _run_event_mapping(event: NormalizedWorkflowRunEvent) -> dict[str, object]:
    if event.created_at is None or event.run_started_at is None or event.updated_at is None:
        raise CiEconomicsCodecError("completed workflow run requires complete timestamps")
    return {
        "schemaVersion": "ci-workflow-run-observation/v1",
        "installationId": event.repository.installation_id,
        "repositoryId": event.repository.repository_id,
        "workflowRunId": event.workflow_run_id,
        "runAttempt": event.run_attempt,
        "workflowId": event.workflow_id,
        "workflowFile": event.workflow_file,
        "workflowName": event.workflow_name,
        "headBranch": event.head_branch,
        "headSha": event.head_sha,
        "workflowEvent": event.workflow_event,
        "status": event.status,
        "conclusion": event.conclusion,
        "createdAt": _format_instant(event.created_at),
        "runStartedAt": _format_instant(event.run_started_at),
        "updatedAt": _format_instant(event.updated_at),
    }


def _runner(value: object) -> RunnerIdentity:
    if value is None:
        return RunnerIdentity(None, None, None, None)
    if type(value) is not dict:
        raise CiEconomicsCodecError("runner must be an object or null")
    runner = cast(dict[str, object], value)
    require_exact_keys(
        runner,
        {"runnerId", "runnerName", "runnerGroupId", "runnerGroupName"},
        "runner",
    )
    return RunnerIdentity(
        _optional_positive(runner["runnerId"], "runnerId"),
        _optional_text(runner["runnerName"], "runnerName"),
        _optional_positive(runner["runnerGroupId"], "runnerGroupId"),
        _optional_text(runner["runnerGroupName"], "runnerGroupName"),
    )


def _format_instant(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CiEconomicsCodecError("workflow observation timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _instant(value: object, name: str) -> datetime:
    text = _text(value, name)
    if not text.endswith("Z"):
        raise CiEconomicsCodecError(f"{name} must be a UTC instant")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as error:
        raise CiEconomicsCodecError(f"{name} is invalid") from error
    if _format_instant(parsed) != text:
        raise CiEconomicsCodecError(f"{name} is not canonical microseconds")
    return parsed


def _positive(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise CiEconomicsCodecError(f"{name} must be a positive integer")
    return value


def _optional_positive(value: object, name: str) -> int | None:
    return None if value is None else _positive(value, name)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise CiEconomicsCodecError(f"{name} must be non-empty text")
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise CiEconomicsCodecError(f"{name} must be an array of text")
    return tuple(cast(list[str], value))
