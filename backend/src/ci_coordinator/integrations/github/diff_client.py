from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    DEFAULT_GITHUB_PAGE,
    GitHubPage,
    GitHubRepository,
    path_value,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome


class DiffClient(GitHubProtocolClient):
    async def get_pull_request(
        self,
        repository: GitHubRepository,
        pull_request_number: int,
    ) -> GitHubOutcome:
        _require_pull_request_number(pull_request_number)
        return await self._get(
            operation="diff.get_pull_request",
            path=f"{repository.path}/pulls/{pull_request_number}",
        )

    async def list_pull_request_files(
        self,
        repository: GitHubRepository,
        pull_request_number: int,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        _require_pull_request_number(pull_request_number)
        return await self._get(
            operation="diff.list_pull_request_files",
            path=f"{repository.path}/pulls/{pull_request_number}/files",
            query=page.query(),
        )

    async def compare(
        self,
        repository: GitHubRepository,
        base: str,
        head: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="diff.compare",
            path=f"{repository.path}/compare/{path_value(base)}...{path_value(head)}",
        )


def _require_pull_request_number(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("GitHub pull request number must be a positive integer")
