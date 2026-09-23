from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import GitHubPage, GitHubRepository, path_value
from ci_coordinator.integrations.github.contracts import GitHubOutcome


class AppIdentityClient(GitHubProtocolClient):
    async def list_installations(self, page: GitHubPage) -> GitHubOutcome:
        return await self._get(
            operation="app_identity.list_installations",
            path="/app/installations",
            query=page.query(),
        )

    async def get_installation(self, installation_id: int) -> GitHubOutcome:
        if installation_id < 1:
            raise ValueError("GitHub installation id must be positive")
        return await self._get(
            operation="app_identity.get_installation",
            path=f"/app/installations/{path_value(str(installation_id))}",
        )

    async def get_repository_installation(
        self,
        repository: GitHubRepository,
    ) -> GitHubOutcome:
        return await self._get(
            operation="app_identity.get_repository_installation",
            path=f"{repository.path}/installation",
        )
