"""Protocol-only Git database requests used by workflow authority admission."""

from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    GitHubRepository,
    path_value,
    repository_id_path,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome


class WorkflowAuthorityClient(GitHubProtocolClient):
    async def get_repository(self, repository_id: int) -> GitHubOutcome:
        return await self._get(
            operation="workflow_authority.get_repository",
            path=repository_id_path(repository_id),
        )

    async def get_commit(
        self,
        repository: GitHubRepository,
        commit_id: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_authority.get_commit",
            path=f"{repository.path}/git/commits/{path_value(commit_id)}",
        )

    async def get_tree(
        self,
        repository: GitHubRepository,
        tree_id: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_authority.get_tree",
            path=f"{repository.path}/git/trees/{path_value(tree_id)}",
        )

    async def get_blob(
        self,
        repository: GitHubRepository,
        blob_id: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_authority.get_blob",
            path=f"{repository.path}/git/blobs/{path_value(blob_id)}",
        )
