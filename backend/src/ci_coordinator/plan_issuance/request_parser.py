from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from ci_coordinator.plan_issuance.model import PLAN_REQUEST_SCHEMA_VERSION, PlanRequest


def parse_plan_request(value: object) -> PlanRequest | str:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        return "plan_request_not_object"
    required = {
        "schemaVersion",
        "requestId",
        "installationId",
        "repositoryId",
        "owner",
        "repository",
        "eventName",
        "ref",
        "baseSha",
        "headSha",
        "executionSha",
        "workflowRunId",
        "runAttempt",
    }
    optional = {"pullRequestNumber", "mergeGroupHeadRef"}
    if set(value) - required - optional:
        return "plan_request_unknown_fields"
    if required - set(value):
        return "plan_request_missing_fields"
    try:
        return PlanRequest(
            schema_version=_schema(value.get("schemaVersion")),
            request_id=_text(value.get("requestId")),
            installation_id=_positive(value.get("installationId")),
            repository_id=_positive(value.get("repositoryId")),
            owner=_text(value.get("owner")),
            repository=_text(value.get("repository")),
            event_name=_event(value.get("eventName")),
            ref=_text(value.get("ref")),
            base_sha=_git_sha(value.get("baseSha")),
            head_sha=_git_sha(value.get("headSha")),
            execution_sha=_git_sha(value.get("executionSha")),
            workflow_run_id=_positive(value.get("workflowRunId")),
            run_attempt=_positive(value.get("runAttempt")),
            pull_request_number=_optional_positive(value.get("pullRequestNumber")),
            merge_group_head_ref=_optional_text(value.get("mergeGroupHeadRef")),
        )
    except ValueError as error:
        return str(error)


def _schema(value: object) -> Literal["dynamic-ci-plan-request/v2"]:
    if value != PLAN_REQUEST_SCHEMA_VERSION:
        raise ValueError("plan_request_schema_unsupported")
    return PLAN_REQUEST_SCHEMA_VERSION


def _text(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("plan_request_invalid_text")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("plan_request_invalid_integer")
    return value


def _optional_positive(value: object) -> int | None:
    if value is None:
        return None
    return _positive(value)


def _event(value: object) -> Literal["pull_request", "push", "merge_group"]:
    if value not in {"pull_request", "push", "merge_group"}:
        raise ValueError("plan_request_event_unsupported")
    return value


def _git_sha(value: object) -> str:
    result = _text(value)
    if not 40 <= len(result) <= 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ValueError("plan_request_invalid_git_sha")
    return result
