from __future__ import annotations

from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    DEFAULT_GITHUB_PAGE,
    GitHubPage,
    GitHubRepository,
    contents_path_value,
    path_value,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome, GitHubQueryParameter


class WorkflowCatalogClient(GitHubProtocolClient):
    async def list_workflows(
        self,
        repository: GitHubRepository,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_catalog.list_workflows",
            path=f"{repository.path}/actions/workflows",
            query=page.query(),
        )

    async def get_workflow(
        self,
        repository: GitHubRepository,
        workflow_id: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="workflow_catalog.get_workflow",
            path=f"{repository.path}/actions/workflows/{path_value(workflow_id)}",
        )

    async def get_content(
        self,
        repository: GitHubRepository,
        path: str,
        *,
        ref: str,
    ) -> GitHubOutcome:
        """Read one repository-owned file at an explicit immutable Git object."""

        if not ref:
            raise ValueError("GitHub contents ref must not be empty")
        return await self._get(
            operation="workflow_catalog.get_content",
            path=f"{repository.path}/contents/{contents_path_value(path)}",
            query=(GitHubQueryParameter(name="ref", value=ref),),
        )
