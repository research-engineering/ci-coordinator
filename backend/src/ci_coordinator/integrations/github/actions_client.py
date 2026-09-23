from __future__ import annotations

from ci_coordinator.ci_economics.discovery import RunDiscoveryWindow
from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._routes import (
    DEFAULT_GITHUB_PAGE,
    GitHubPage,
    GitHubRepository,
    path_value,
    repository_id_path,
    workflow_run_attempt_jobs_path,
    workflow_run_attempt_path,
    workflow_run_created_query,
    workflow_run_path,
)
from ci_coordinator.integrations.github.contracts import GitHubOutcome


class ActionsClient(GitHubProtocolClient):
    async def get_workflow_run(
        self, repository: GitHubRepository, workflow_run_id: int
    ) -> GitHubOutcome:
        return await self._get(
            operation="actions.get_workflow_run",
            path=workflow_run_path(repository, workflow_run_id),
        )

    async def list_repository_workflow_runs(
        self,
        repository: GitHubRepository,
        window: RunDiscoveryWindow,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="actions.list_repository_workflow_runs",
            path=f"{repository.path}/actions/runs",
            query=(workflow_run_created_query(window), *page.query()),
        )

    async def get_repository_by_id(self, repository_id: int) -> GitHubOutcome:
        return await self._get(
            operation="repositories.get_by_id",
            path=repository_id_path(repository_id),
        )

    async def list_workflow_runs(
        self,
        repository: GitHubRepository,
        workflow_id: str,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="actions.list_workflow_runs",
            path=f"{repository.path}/actions/workflows/{path_value(workflow_id)}/runs",
            query=page.query(),
        )

    async def list_workflow_run_attempt_jobs(
        self,
        repository: GitHubRepository,
        workflow_run_id: int,
        run_attempt: int,
        *,
        page: GitHubPage = DEFAULT_GITHUB_PAGE,
    ) -> GitHubOutcome:
        return await self._get(
            operation="actions.list_workflow_run_attempt_jobs",
            path=workflow_run_attempt_jobs_path(repository, workflow_run_id, run_attempt),
            query=page.query(),
        )

    async def get_workflow_run_attempt(
        self,
        repository: GitHubRepository,
        workflow_run_id: int,
        run_attempt: int,
    ) -> GitHubOutcome:
        return await self._get(
            operation="actions.get_workflow_run_attempt",
            path=workflow_run_attempt_path(repository, workflow_run_id, run_attempt),
        )
