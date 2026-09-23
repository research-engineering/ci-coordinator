from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    DEFAULT_GITHUB_PAGE,
    GitHubPage,
    GitHubRepository,
    path_value,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome


class ChecksClient(GitHubProtocolClient):
    async def list_check_runs(
        self,
        repository: GitHubRepository,
        ref: str,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="checks.list_check_runs",
            path=f"{repository.path}/commits/{path_value(ref)}/check-runs",
            query=page.query(),
        )
