"""Protocol-only Git database requests used by workflow discovery."""

from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    GitHubRepository,
    contents_path_value,
    path_value,
    repository_id_path,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome, GitHubQueryParameter


class WorkflowDiscoveryClient(GitHubProtocolClient):
    async def get_repository(self, repository_id: int) -> GitHubOutcome:
        return await self._get(
            operation="workflow_discovery.get_repository",
            path=repository_id_path(repository_id),
        )

    async def get_reference(
        self,
        repository: GitHubRepository,
        ref: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_discovery.get_reference",
            path=f"{repository.path}/git/ref/{contents_path_value(ref)}",
        )

    async def get_commit(
        self,
        repository: GitHubRepository,
        commit_sha: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_discovery.get_commit",
            path=f"{repository.path}/git/commits/{path_value(commit_sha)}",
        )

    async def get_tree(
        self,
        repository: GitHubRepository,
        tree_sha: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_discovery.get_tree",
            path=f"{repository.path}/git/trees/{path_value(tree_sha)}",
        )

    async def get_recursive_tree(
        self,
        repository: GitHubRepository,
        tree_sha: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_discovery.get_recursive_tree",
            path=f"{repository.path}/git/trees/{path_value(tree_sha)}",
            query=(GitHubQueryParameter("recursive", "1"),),
        )

    async def get_blob(
        self,
        repository: GitHubRepository,
        blob_sha: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_discovery.get_blob",
            path=f"{repository.path}/git/blobs/{path_value(blob_sha)}",
        )
