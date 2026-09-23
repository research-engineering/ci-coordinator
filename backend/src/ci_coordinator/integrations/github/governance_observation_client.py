"""Protocol-only GitHub requests for effective-governance observation."""

from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    GitHubRepository,
    path_value,
    repository_id_path,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome


class GovernanceObservationClient(GitHubProtocolClient):
    async def get_repository(self, repository_id: int) -> GitHubOutcome:
        return await self._get(
            operation="governance_observation.get_repository",
            path=repository_id_path(repository_id),
        )

    async def list_effective_branch_rules(
        self,
        repository: GitHubRepository,
        *,
        branch: str,
        page: GitHubPage,
    ) -> GitHubOutcome:
        return await self._get(
            operation="governance_observation.list_effective_branch_rules",
            path=effective_branch_rules_path(repository, branch),
            query=page.query(),
        )


def effective_branch_rules_path(repository: GitHubRepository, branch: str) -> str:
    return f"{repository.path}/rules/branches/{path_value(branch)}"
