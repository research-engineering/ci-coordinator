from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    DEFAULT_GITHUB_PAGE,
    GitHubPage,
    GitHubRepository,
    organization_runner_group_runners_path,
    organization_runner_groups_path,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome, GitHubQueryParameter


class RunnerClient(GitHubProtocolClient):
    async def list_self_hosted_runners(
        self,
        repository: GitHubRepository,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="runner.list_self_hosted_runners",
            path=f"{repository.path}/actions/runners",
            query=page.query(),
        )

    async def list_visible_self_hosted_runner_groups(
        self,
        repository: GitHubRepository,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="runner.list_visible_self_hosted_runner_groups",
            path=organization_runner_groups_path(repository.owner),
            query=(
                GitHubQueryParameter(
                    name="visible_to_repository",
                    value=repository.name,
                ),
                *page.query(),
            ),
        )

    async def list_group_self_hosted_runners(
        self,
        organization: str,
        runner_group_id: int,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="runner.list_group_self_hosted_runners",
            path=organization_runner_group_runners_path(
                organization,
                runner_group_id,
            ),
            query=page.query(),
        )
