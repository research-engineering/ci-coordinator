from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from ci_coordinator.ci_economics.discovery import RunDiscoveryWindow
from ci_coordinator.integrations.github.contracts import GitHubQueryParameter

_MAX_SAFE_PATH_INTEGER = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    owner: str
    name: str

    def __post_init__(self) -> None:
        if not self.owner or not self.name:
            raise ValueError("GitHub repository owner and name must not be empty")

    @property
    def path(self) -> str:
        return f"/repos/{quote(self.owner, safe='')}/{quote(self.name, safe='')}"


@dataclass(frozen=True, slots=True)
class GitHubPage:
    number: int = 1
    size: int = 100

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("GitHub page number must be positive")
        if not 1 <= self.size <= 100:
            raise ValueError("GitHub page size must be between 1 and 100")

    def query(self) -> tuple[GitHubQueryParameter, GitHubQueryParameter]:
        return (
            GitHubQueryParameter(name="page", value=str(self.number)),
            GitHubQueryParameter(name="per_page", value=str(self.size)),
        )


DEFAULT_GITHUB_PAGE = GitHubPage()


def repository_path_matches(
    observed: str, expected: str, *, repository_id: int | None = None
) -> bool:
    if repository_id is not None and (
        type(repository_id) is not int or not 1 <= repository_id <= _MAX_SAFE_PATH_INTEGER
    ):
        return False
    if observed == expected:
        return True
    parts = expected.split("/", 4)
    return (
        repository_id is not None
        and len(parts) == 5
        and parts[:2] == ["", "repos"]
        and bool(parts[2])
        and bool(parts[3])
        and observed == f"/repositories/{repository_id}/{parts[4]}"
    )


def workflow_run_created_query(window: RunDiscoveryWindow) -> GitHubQueryParameter:
    start = window.created_from.isoformat().replace("+00:00", "Z")
    end = window.created_through.isoformat().replace("+00:00", "Z")
    return GitHubQueryParameter("created", f"{start}..{end}")


def parse_workflow_run_created_query(value: str) -> RunDiscoveryWindow | None:
    if type(value) is not str or len(value) != 42 or value[20:22] != "..":
        return None
    try:
        window = RunDiscoveryWindow(
            datetime.fromisoformat(value[:20]), datetime.fromisoformat(value[22:])
        )
    except ValueError:
        return None
    return window if workflow_run_created_query(window).value == value else None


def path_value(value: str) -> str:
    if not value:
        raise ValueError("GitHub path value must not be empty")
    return quote(value, safe="")


def contents_path_value(value: str) -> str:
    """Encode a repository-relative Contents API path without collapsing segments."""

    if not value:
        raise ValueError("GitHub contents path must not be empty")
    return "/".join(path_value(segment) for segment in value.split("/"))


def repository_id_path(repository_id: int) -> str:
    return f"/repositories/{_positive_path_integer(repository_id, 'repository id')}"


def organization_runner_groups_path(organization: str) -> str:
    return f"/orgs/{path_value(organization)}/actions/runner-groups"


def organization_runner_group_runners_path(
    organization: str,
    runner_group_id: int,
) -> str:
    group_id = _positive_path_integer(runner_group_id, "runner group id")
    return f"{organization_runner_groups_path(organization)}/{group_id}/runners"


def workflow_run_attempt_jobs_path(
    repository: GitHubRepository,
    workflow_run_id: int,
    run_attempt: int,
) -> str:
    return f"{workflow_run_attempt_path(repository, workflow_run_id, run_attempt)}/jobs"


def workflow_run_attempt_path(
    repository: GitHubRepository,
    workflow_run_id: int,
    run_attempt: int,
) -> str:
    path = workflow_run_path(repository, workflow_run_id)
    attempt = _positive_path_integer(run_attempt, "workflow run attempt")
    return f"{path}/attempts/{attempt}"


def workflow_run_path(repository: GitHubRepository, workflow_run_id: int) -> str:
    run_id = _positive_path_integer(workflow_run_id, "workflow run id")
    return f"{repository.path}/actions/runs/{run_id}"


def _positive_path_integer(value: int, field_name: str) -> str:
    if type(value) is not int or not 1 <= value <= _MAX_SAFE_PATH_INTEGER:
        raise ValueError(f"GitHub {field_name} must be a positive safe integer")
    return str(value)
