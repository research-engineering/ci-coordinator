"""Exact Git-object admission for one target workflow adapter revision."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final

from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    MAX_TARGET_EXECUTION_REGISTRY_BYTES,
    TARGET_EXECUTION_REGISTRY_PATH,
    TargetExecutionRegistry,
    digest_adapter_file,
    parse_target_execution_registry,
)
from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.git_snapshot import (
    GitHubGitSnapshotReader,
    regular_blob_entry,
)
from ci_coordinator.integrations.github.workflow_discovery_client import (
    WorkflowDiscoveryClient,
)
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    GitTreeEntry,
    is_github_object_id,
)
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context import (
    MAX_WORKFLOW_FILE_BYTES,
    ProviderWorkflowInventory,
    parse_workflow_capability,
)
from ci_coordinator.target_artifacts.requester import render_plan_requester

_MAX_CONTROL_FILE_BYTES: Final = 1_048_576
_MAX_CONCURRENT_BLOBS: Final = 4


class _AdapterBlobRejected(Exception):
    pass


@dataclass(frozen=True, slots=True)
class GitHubAdapterSnapshot:
    registry: TargetExecutionRegistry
    workflow_inventory: ProviderWorkflowInventory

    def __post_init__(self) -> None:
        if type(self.registry) is not TargetExecutionRegistry:
            raise TypeError("adapter snapshot registry must be exact")
        if type(self.workflow_inventory) is not ProviderWorkflowInventory:
            raise TypeError("adapter snapshot inventory must be exact")


class GitHubAdapterSnapshotLoader:
    """Read one bounded adapter from regular blobs at an immutable commit."""

    def __init__(self, client: WorkflowDiscoveryClient) -> None:
        if type(client) is not WorkflowDiscoveryClient:
            raise TypeError("adapter snapshot loader requires an exact Git client")
        self._reader = GitHubGitSnapshotReader(client)

    async def load(
        self,
        repository: GitHubRepository,
        *,
        revision_sha: str,
    ) -> GitHubAdapterSnapshot | None:
        if type(repository) is not GitHubRepository:
            raise TypeError("adapter snapshot requires an exact repository")
        if not is_github_object_id(revision_sha):
            raise ValueError("adapter snapshot requires an immutable revision")

        root = await self._reader.root_tree(repository, revision_sha)
        if root is None:
            return None
        control_tree = await self._reader.child_tree(repository, root, ".ci-coordinator")
        if control_tree is None:
            return None
        registry_entry = regular_blob_entry(
            control_tree,
            TARGET_EXECUTION_REGISTRY_PATH.rsplit("/", 1)[1],
            maximum_bytes=MAX_TARGET_EXECUTION_REGISTRY_BYTES,
        )
        if registry_entry is None:
            return None
        registry_content = await self._reader.blob(
            repository,
            registry_entry,
            maximum_bytes=MAX_TARGET_EXECUTION_REGISTRY_BYTES,
        )
        if registry_content is None:
            return None
        registry = parse_target_execution_registry(registry_content)
        if registry is None:
            return None

        github_tree = await self._reader.child_tree(repository, root, ".github")
        if github_tree is None:
            return None
        workflow_tree = await self._reader.child_tree(repository, github_tree, "workflows")
        if workflow_tree is None:
            return None

        entries: list[tuple[str, GitTreeEntry, int, str]] = []
        for binding in registry.adapter_files:
            directory = workflow_tree if binding.is_workflow else control_tree
            maximum = MAX_WORKFLOW_FILE_BYTES if binding.is_workflow else _MAX_CONTROL_FILE_BYTES
            entry = regular_blob_entry(
                directory,
                binding.path.rsplit("/", 1)[1],
                maximum_bytes=maximum,
            )
            if entry is None:
                return None
            entries.append((binding.path, entry, maximum, binding.sha256))

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BLOBS)

        async def load_bound(
            path: str,
            entry: GitTreeEntry,
            maximum: int,
            expected_sha256: str,
        ) -> tuple[str, bytes]:
            async with semaphore:
                content = await self._reader.blob(
                    repository,
                    entry,
                    maximum_bytes=maximum,
                )
            if content is None or digest_adapter_file(content) != expected_sha256:
                raise _AdapterBlobRejected
            return path, content

        tasks: tuple[asyncio.Task[tuple[str, bytes]], ...] = ()
        rejected = False
        try:
            async with asyncio.TaskGroup() as task_group:
                tasks = tuple(
                    task_group.create_task(load_bound(path, entry, maximum, expected_sha256))
                    for path, entry, maximum, expected_sha256 in entries
                )
        except* _AdapterBlobRejected:
            rejected = True
        if rejected:
            return None
        loaded = tuple(task.result() for task in tasks)
        content_by_path = dict(loaded)
        if (
            any(
                workflow.plan_request_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF
                for workflow in registry.workflows
            )
            and content_by_path.get(LOCAL_PLAN_REQUEST_WORKFLOW_PATH) != render_plan_requester()
        ):
            return None
        capabilities = []
        for binding in registry.adapter_files:
            if not binding.is_workflow:
                continue
            capability = parse_workflow_capability(
                content_by_path[binding.path],
                path=binding.path,
                revision_sha=revision_sha,
            )
            if capability is None:
                return None
            capabilities.append(capability)
        try:
            inventory = ProviderWorkflowInventory(
                revision_sha=revision_sha,
                default_branch_workflows=(),
                revision_capabilities=tuple(
                    sorted(capabilities, key=lambda item: utf16_sort_key(item.path))
                ),
            )
            return GitHubAdapterSnapshot(registry, inventory)
        except (TypeError, ValueError):
            return None
