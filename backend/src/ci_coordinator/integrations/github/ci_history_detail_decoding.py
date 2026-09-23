from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from pydantic import TypeAdapter

from ci_coordinator.ci_economics.archive_detail import (
    MAX_HISTORY_STEPS_PER_JOB,
    ArchivedJobDetail,
    ArchiveStepDetail,
)
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.model import WorkflowConclusion
from ci_coordinator.ci_economics.observation_payload import ObservationTimestamp
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github.ci_history_decoding import _ACTIVE_STATUSES
from ci_coordinator.integrations.github.reconciliation_observer_pagination import JOBS_PAGE_SIZE
from ci_coordinator.kernel.canonical_json import bounded_canonical_json

_TIMESTAMP: TypeAdapter[str] = TypeAdapter(ObservationTimestamp)
_MISSING = object()


@dataclass(frozen=True, slots=True)
class HistoryDetailPage:
    jobs: tuple[ArchivedJobDetail, ...]
    consumed_bytes: int


def decode_history_detail_page(
    body: bytes, cursor: HistoryAttemptCursor, *, head_sha: str, remaining_bytes: int
) -> HistoryDetailPage | None:
    raw = json_object_or_none(body)
    raw_jobs = None if raw is None else raw.get("jobs")
    if type(raw_jobs) is not list or len(raw_jobs) > JOBS_PAGE_SIZE:
        return None
    details: list[ArchivedJobDetail] = []
    consumed_bytes = 0
    for raw_job in raw_jobs:
        value = object_or_none(raw_job)
        if value is None:
            return None
        status = value.get("status")
        if status in _ACTIVE_STATUSES or status != "completed":
            return None
        if (
            positive_safe_integer(value.get("run_id")) != cursor.workflow_run_id
            or value.get("head_sha") != head_sha
        ):
            return None
        if "run_attempt" in value and (
            positive_safe_integer(value["run_attempt"]) != cursor.next_attempt
        ):
            return None
        job_id = positive_safe_integer(value.get("id"))
        raw_steps = value.get("steps", _MISSING)
        if job_id is None or raw_steps is _MISSING or type(raw_steps) is not list:
            return None
        if len(raw_steps) > MAX_HISTORY_STEPS_PER_JOB:
            return None
        steps: list[ArchiveStepDetail] = []
        for raw_step in raw_steps:
            step = object_or_none(raw_step)
            if step is None or any(
                field not in step
                for field in ("number", "status", "conclusion", "started_at", "completed_at")
            ):
                return None
            number = positive_safe_integer(step["number"])
            if number is None or step["status"] != "completed":
                return None
            try:
                steps.append(
                    ArchiveStepDetail(
                        number=number,
                        status="completed",
                        conclusion=cast(WorkflowConclusion | None, step["conclusion"]),
                        startedAt=_timestamp(step["started_at"]),
                        completedAt=_timestamp(step["completed_at"]),
                    )
                )
            except (TypeError, ValueError):
                return None
        try:
            detail = ArchivedJobDetail(providerJobId=job_id, steps=tuple(steps))
            encoded = bounded_canonical_json(
                detail.model_dump(mode="json"),
                max_bytes=remaining_bytes - consumed_bytes - 1,
            )
        except (TypeError, ValueError):
            return None
        consumed_bytes += len(encoded) + 1
        details.append(detail)
    return HistoryDetailPage(tuple(details), consumed_bytes)


def _timestamp(value: object) -> datetime | None:
    return (
        None
        if value is None
        else datetime.fromisoformat(_TIMESTAMP.validate_python(value, strict=True))
    )
