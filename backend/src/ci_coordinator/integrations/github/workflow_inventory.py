"""Complete default-branch workflow index and exact-revision job admission."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlsplit

from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    GitHubRepository,
    contents_path_value,
    repository_path_matches,
)
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_BASE_URL,
    GITHUB_API_VERSION,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.repository_context_decoding import decode_contents_file
from ci_coordinator.integrations.github.workflow_catalog_client import WorkflowCatalogClient
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context import (
    MAX_WORKFLOW_FILE_BYTES,
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    ProviderWorkflowInventoryEvidence,
    RevisionWorkflowCapability,
    is_git_object_revision,
    is_workflow_path,
    parse_workflow_capability,
)
from ci_coordinator.workflow_authority import WorkflowAuthorityRepository

WORKFLOW_INVENTORY_PAGE_SIZE: Final = 100
_MAX_NEXT_PAGE_URL_BYTES: Final = 2_048
_MAX_WORKFLOW_CONTENT_RESPONSE_BYTES: Final = 524_288
_PLATFORM_WORKFLOW_PATHS: Final = frozenset(
    {"dynamic/dependabot/update-graph", "dynamic/github-code-scanning/codeql"}
)


@dataclass(frozen=True, slots=True)
class WorkflowInventoryLimits:
    max_pages: int = 20
    max_workflows: int = 2_000
    max_total_index_bytes: int = 8_388_608

    def __post_init__(self) -> None:
        for value in (self.max_pages, self.max_workflows, self.max_total_index_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("workflow inventory limits must be positive integers")


DEFAULT_WORKFLOW_INVENTORY_LIMITS: Final = WorkflowInventoryLimits()


@dataclass(frozen=True, slots=True)
class _PlatformWorkflow:
    workflow_id: int
    path: str


@dataclass(frozen=True, slots=True)
class _WorkflowPage:
    total_count: int
    workflows: tuple[DefaultBranchWorkflow | _PlatformWorkflow, ...]


class GitHubWorkflowInventoryLoader:
    """Load only evidence proven complete by GitHub pagination and exact refs."""

    def __init__(
        self,
        client: WorkflowCatalogClient,
        *,
        limits: WorkflowInventoryLimits = DEFAULT_WORKFLOW_INVENTORY_LIMITS,
    ) -> None:
        if type(client) is not WorkflowCatalogClient:
            raise TypeError("workflow inventory requires an exact workflow client")
        if type(limits) is not WorkflowInventoryLimits:
            raise TypeError("workflow inventory requires exact limits")
        self._client = client
        self._limits = limits

    async def load(
        self,
        repository: WorkflowAuthorityRepository,
        *,
        revision_sha: str,
        required_paths: tuple[str, ...],
    ) -> ProviderWorkflowInventoryEvidence | None:
        if type(repository) is not WorkflowAuthorityRepository:
            raise TypeError("workflow inventory requires an exact subject-bound repository")
        if not is_git_object_revision(revision_sha):
            raise ValueError("workflow inventory requires an immutable Git revision")
        if type(required_paths) is not tuple or any(
            not is_workflow_path(path) for path in required_paths
        ):
            raise ValueError("required workflow paths must be bounded workflow files")
        canonical_paths = tuple(sorted(set(required_paths), key=utf16_sort_key))
        if not canonical_paths or canonical_paths != required_paths or len(canonical_paths) > 64:
            raise ValueError("required workflow paths must be non-empty and canonical")

        provider_repository = GitHubRepository(repository.owner, repository.name)
        workflows = await self._list_default_branch_workflows(
            provider_repository, repository.scope.repository_id
        )
        if workflows is None:
            return None
        active_paths = {item.path for item in workflows if item.active}
        if not set(canonical_paths).issubset(active_paths):
            return None

        capabilities: list[RevisionWorkflowCapability] = []
        for path in canonical_paths:
            content = await self._load_exact_content(provider_repository, path, revision_sha)
            if content is None:
                return None
            capability = parse_workflow_capability(
                content,
                path=path,
                revision_sha=revision_sha,
            )
            if capability is None:
                return None
            capabilities.append(capability)
        try:
            inventory = ProviderWorkflowInventory(
                revision_sha=revision_sha,
                default_branch_workflows=workflows,
                revision_capabilities=tuple(capabilities),
            )
            return ProviderWorkflowInventoryEvidence(
                scope=repository.scope,
                owner=repository.owner,
                name=repository.name,
                default_branch=repository.default_branch,
                api_version=GITHUB_API_VERSION,
                inventory=inventory,
            )
        except (TypeError, ValueError):
            return None

    async def _list_default_branch_workflows(
        self,
        repository: GitHubRepository,
        repository_id: int,
    ) -> tuple[DefaultBranchWorkflow, ...] | None:
        expected_path = f"{repository.path}/actions/workflows"
        page_number = 1
        pages_observed = 0
        response_bytes = 0
        expected_total: int | None = None
        workflows_by_path: dict[str, DefaultBranchWorkflow | _PlatformWorkflow] = {}
        workflow_ids: set[int] = set()

        while True:
            try:
                outcome = await self._client.list_workflows(
                    repository,
                    page=GitHubPage(page_number, WORKFLOW_INVENTORY_PAGE_SIZE),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return None
            pages_observed += 1
            if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)):
                return None
            request = outcome.request
            if (
                request.operation != "workflow_catalog.list_workflows"
                or request.method != "GET"
                or request.path != expected_path
                or request.api_version != GITHUB_API_VERSION
                or request.query != GitHubPage(page_number, WORKFLOW_INVENTORY_PAGE_SIZE).query()
                or request.body is not None
            ):
                return None

            body = outcome.response.body
            if type(body) is not bytes or response_bytes > (
                self._limits.max_total_index_bytes - len(body)
            ):
                return None
            response_bytes += len(body)
            page = _decode_workflow_page(body)
            if page is None or page.total_count > self._limits.max_workflows:
                return None
            if expected_total is None:
                expected_total = page.total_count
            elif page.total_count != expected_total:
                return None

            for workflow in page.workflows:
                if workflow.path in workflows_by_path or workflow.workflow_id in workflow_ids:
                    return None
                workflows_by_path[workflow.path] = workflow
                workflow_ids.add(workflow.workflow_id)
            if len(workflows_by_path) > expected_total:
                return None

            pagination = outcome.response.pagination
            if pagination.item_count is not None and pagination.item_count != expected_total:
                return None
            if isinstance(outcome, GitHubSuccess):
                if (
                    pagination.complete is not True
                    or pagination.pages_observed != 1
                    or pagination.next_page is not None
                    or pagination.termination not in {"not_paginated", "exhausted"}
                    or len(workflows_by_path) != expected_total
                ):
                    return None
                return tuple(
                    workflow
                    for workflow in sorted(
                        workflows_by_path.values(), key=lambda item: utf16_sort_key(item.path)
                    )
                    if isinstance(workflow, DefaultBranchWorkflow)
                )

            if pages_observed >= self._limits.max_pages or len(workflows_by_path) >= expected_total:
                return None
            next_page = _next_page_number(
                outcome.response.pagination,
                expected_path=expected_path,
                current_page=page_number,
                repository_id=repository_id,
            )
            if next_page is None:
                return None
            page_number = next_page

    async def _load_exact_content(
        self,
        repository: GitHubRepository,
        path: str,
        revision_sha: str,
    ) -> bytes | None:
        try:
            outcome = await self._client.get_content(repository, path, ref=revision_sha)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if not isinstance(outcome, GitHubSuccess):
            return None
        request = outcome.request
        if (
            request.operation != "workflow_catalog.get_content"
            or request.method != "GET"
            or request.api_version != GITHUB_API_VERSION
            or request.query != (GitHubQueryParameter("ref", revision_sha),)
        ):
            return None
        expected_path = f"{repository.path}/contents/{contents_path_value(path)}"
        if request.path != expected_path or request.body is not None:
            return None
        return decode_contents_file(
            outcome.response.body,
            expected_path=path,
            max_json_bytes=_MAX_WORKFLOW_CONTENT_RESPONSE_BYTES,
            max_content_bytes=MAX_WORKFLOW_FILE_BYTES,
        )


def _decode_workflow_page(body: bytes) -> _WorkflowPage | None:
    value = json_object_or_none(body)
    if value is None:
        return None
    total_count = non_negative_safe_integer(value.get("total_count"))
    raw_workflows = value.get("workflows")
    if (
        total_count is None
        or type(raw_workflows) is not list
        or len(raw_workflows) > WORKFLOW_INVENTORY_PAGE_SIZE
    ):
        return None
    workflows: list[DefaultBranchWorkflow | _PlatformWorkflow] = []
    for raw_workflow in raw_workflows:
        value = object_or_none(raw_workflow)
        if value is None:
            return None
        workflow_id = positive_safe_integer(value.get("id"))
        path = value.get("path")
        state = value.get("state")
        if (
            workflow_id is None
            or type(path) is not str
            or type(state) is not str
            or not state
            or len(state.encode("utf-8")) > 64
        ):
            return None
        if path in _PLATFORM_WORKFLOW_PATHS:
            workflows.append(_PlatformWorkflow(workflow_id, path))
            continue
        try:
            workflows.append(DefaultBranchWorkflow(workflow_id, path, state == "active"))
        except (TypeError, ValueError):
            return None
    return _WorkflowPage(total_count, tuple(workflows))


def _next_page_number(
    pagination: GitHubPaginationEvidence,
    *,
    expected_path: str,
    current_page: int,
    repository_id: int | None = None,
) -> int | None:
    if type(pagination) is not GitHubPaginationEvidence:
        return None
    next_page = pagination.next_page
    if (
        pagination.complete is not False
        or pagination.pages_observed != 1
        or pagination.termination != "next_page"
        or type(next_page) is not str
        or not next_page.isascii()
        or len(next_page.encode("ascii")) > _MAX_NEXT_PAGE_URL_BYTES
    ):
        return None
    try:
        target = urlsplit(next_page)
        origin = urlsplit(GITHUB_API_BASE_URL)
        parameters = parse_qsl(
            target.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
        target_port = target.port
    except (UnicodeError, ValueError):
        return None
    if (
        target.scheme != origin.scheme
        or target.hostname != origin.hostname
        or target_port is not None
        or target.username is not None
        or target.password is not None
        or not repository_path_matches(target.path, expected_path, repository_id=repository_id)
        or target.fragment
        or len(parameters) != 2
    ):
        return None
    query = dict(parameters)
    expected_page = current_page + 1
    if (
        len(query) != 2
        or query.get("per_page") != str(WORKFLOW_INVENTORY_PAGE_SIZE)
        or query.get("page") != str(expected_page)
    ):
        return None
    return expected_page
