from dataclasses import replace
from datetime import UTC, datetime

from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.integrations.github.contracts import GitHubPaginationEvidence, GitHubResponse

from ._economics_source_support import ATTEMPT, PATH, RUN_ID, SCOPE, response, run

CURSOR = HistoryAttemptCursor(SCOPE, RUN_ID, ATTEMPT, ATTEMPT)
RUN_CREATED_AT = datetime(2026, 9, 7, 12, tzinfo=UTC)
JOBS_PATH = f"{PATH}/jobs"


def history_run() -> dict[str, object]:
    return {**run(), "workflow_id": 404, "path": ".github/workflows/full.yml@main", "event": "push"}


def history_job(job_id: int = 1) -> dict[str, object]:
    return {
        "id": job_id,
        "run_id": RUN_ID,
        "run_attempt": ATTEMPT,
        "head_sha": "b" * 40,
        "name": "Lint",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-09-07T12:00:00Z",
        "started_at": "2026-09-07T12:00:01Z",
        "completed_at": "2026-09-07T12:00:04Z",
        "labels": ["self-hosted", "linux"],
        "runner_id": 50,
        "runner_group_id": 60,
        "runner_name": "private-worker-name",
        "runner_group_name": "private-group-name",
        "steps": [{"name": "private-step-name"}],
    }


def job_page(
    jobs: list[dict[str, object]], *, total: int | None = None, number: int = 1
) -> GitHubResponse:
    count = len(jobs) if total is None else total
    result = response({"total_count": count, "jobs": jobs})
    if number * 100 < count:
        return replace(
            result,
            pagination=GitHubPaginationEvidence(
                False,
                1,
                count,
                f"https://api.github.com{JOBS_PATH}?page={number + 1}&per_page=100",
                "next_page",
            ),
        )
    return result
