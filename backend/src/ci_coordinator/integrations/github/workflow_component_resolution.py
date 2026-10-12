"""Installation-scoped reads that resolve remote workflow components to exact commits."""

from __future__ import annotations

from typing import Final

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github._routes import (
    GitHubRepository,
    contents_path_value,
    path_value,
    repository_id_path,
)
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubOutcome,
    GitHubSuccess,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.provider_inventory import GitHubProviderInventory
from ci_coordinator.integrations.github.request_admission import (
    get_request_matches as _request_matches,
)
from ci_coordinator.integrations.github.workflow_discovery_client import WorkflowDiscoveryClient
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    GitTree,
    decode_discovery_repository,
    decode_git_commit,
    decode_git_reference_target,
    decode_git_tree,
    is_github_object_id,
)
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.provider_inventory import ProviderInventoryUnavailable
from ci_coordinator.workflow_discovery.component_passport import (
    ComponentLocationOutcome,
    ComponentReadFailure,
    ComponentReadFailureReason,
    ComponentRepositoryLocation,
    ComponentRepositoryScope,
    ComponentRevisionObserved,
    ComponentRevisionOutcome,
    ComponentWorkflowBlob,
)

COMPONENT_INVENTORY_PAGE_SIZE: Final = 100
COMPONENT_INVENTORY_MAXIMUM_PAGES: Final = 10
_MAX_JSON_BYTES = 1_048_576
_MAX_TREE_ENTRIES = 4_096
# GitHub resolves a reusable-workflow ref that names both a tag and a branch to the tag.
_REF_NAMESPACES = ("tags", "heads")


class GitHubComponentRevisionResolver:
    def __init__(
        self,
        transport_factory: GitHubAppTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("component resolution API version must be non-empty")
        self._transport_factory = transport_factory
        self._api_version = api_version
        self._inventory = GitHubProviderInventory(transport_factory, api_version=api_version)

    async def locate_repositories(
        self,
        *,
        installation_id: int,
        owner: str,
        names: tuple[str, ...],
    ) -> ComponentLocationOutcome:
        wanted = {name.lower() for name in names}
        owner_key = owner.lower()
        found: dict[str, ComponentRepositoryScope] = {}
        for page in range(1, COMPONENT_INVENTORY_MAXIMUM_PAGES + 1):
            result = await self._inventory.list_repositories(
                installation_id,
                page=page,
                per_page=COMPONENT_INVENTORY_PAGE_SIZE,
            )
            if isinstance(result, ProviderInventoryUnavailable):
                return ComponentReadFailure(
                    "rate_limited"
                    if result.reason == "rate_limited"
                    else "installation_inventory_unavailable"
                )
            for repository in result.repositories:
                key = repository.name.lower()
                if (
                    repository.owner_login.lower() == owner_key
                    and key in wanted
                    and key not in found
                ):
                    found[key] = ComponentRepositoryScope(repository.name, repository.scope)
            if not result.has_next_page or wanted <= found.keys():
                return _location(found, complete=True)
        return _location(found, complete=False)

    async def read_revision(
        self,
        *,
        scope: RepositoryScope,
        repository: str,
        ref: str,
        workflow_files: tuple[str, ...],
    ) -> ComponentRevisionOutcome:
        owner, _, name = repository.partition("/")
        client = WorkflowDiscoveryClient(
            self._transport_factory.for_installation(
                scope.installation_id, repository_id=scope.repository_id
            ),
            api_version=self._api_version,
        )
        bound = await self._repository(client, scope.repository_id)
        if isinstance(bound, ComponentReadFailure):
            return bound
        if (bound.owner.lower(), bound.name.lower()) != (owner.lower(), name.lower()):
            return ComponentReadFailure("provider_binding_mismatch")
        commit_sha = ref if is_github_object_id(ref) else await self._reference(client, bound, ref)
        if isinstance(commit_sha, ComponentReadFailure):
            return commit_sha
        tree_sha = await self._commit(client, bound, commit_sha)
        if isinstance(tree_sha, ComponentReadFailure):
            return tree_sha
        blobs = await self._workflow_blobs(client, bound, tree_sha, workflow_files)
        if isinstance(blobs, ComponentReadFailure):
            return blobs
        return ComponentRevisionObserved(commit_sha, blobs)

    async def _repository(
        self,
        client: WorkflowDiscoveryClient,
        repository_id: int,
    ) -> GitHubRepository | ComponentReadFailure:
        outcome = self._success(
            await client.get_repository(repository_id),
            operation="workflow_discovery.get_repository",
            path=repository_id_path(repository_id),
            not_found="repository_not_found",
        )
        if isinstance(outcome, ComponentReadFailure):
            return outcome
        decoded = decode_discovery_repository(outcome.response.body, max_json_bytes=_MAX_JSON_BYTES)
        if decoded is None:
            return ComponentReadFailure("malformed_provider_response")
        if decoded.repository_id != repository_id:
            return ComponentReadFailure("provider_binding_mismatch")
        return decoded.repository

    async def _reference(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        ref: str,
    ) -> str | ComponentReadFailure:
        for namespace in _REF_NAMESPACES:
            qualified = f"{namespace}/{ref}"
            response = await client.get_reference(repository, qualified)
            if isinstance(response, GitHubUnavailable) and response.failure.kind == "not_found":
                continue
            outcome = self._success(
                response,
                operation="workflow_discovery.get_reference",
                path=f"{repository.path}/git/ref/{contents_path_value(qualified)}",
                not_found="ref_not_found",
            )
            if isinstance(outcome, ComponentReadFailure):
                return outcome
            target = decode_git_reference_target(
                outcome.response.body,
                expected_ref=qualified,
                max_json_bytes=_MAX_JSON_BYTES,
            )
            if target is None:
                return ComponentReadFailure("malformed_provider_response")
            if target.object_type == "tag":
                return ComponentReadFailure("annotated_tag_not_dereferenced")
            return target.object_sha
        return ComponentReadFailure("ref_not_found")

    async def _commit(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        commit_sha: str,
    ) -> str | ComponentReadFailure:
        outcome = self._success(
            await client.get_commit(repository, commit_sha),
            operation="workflow_discovery.get_commit",
            path=f"{repository.path}/git/commits/{path_value(commit_sha)}",
            not_found="ref_not_found",
        )
        if isinstance(outcome, ComponentReadFailure):
            return outcome
        commit = decode_git_commit(
            outcome.response.body,
            expected_commit_sha=commit_sha,
            max_json_bytes=_MAX_JSON_BYTES,
        )
        return (
            ComponentReadFailure("provider_binding_mismatch") if commit is None else commit.tree_sha
        )

    async def _workflow_blobs(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        root_tree_sha: str,
        workflow_files: tuple[str, ...],
    ) -> tuple[ComponentWorkflowBlob, ...] | ComponentReadFailure:
        if not workflow_files:
            return ()
        tree = await self._tree(client, repository, root_tree_sha)
        for directory in (".github", "workflows"):
            if isinstance(tree, ComponentReadFailure):
                return tree
            entry = next((item for item in tree.entries if item.name == directory), None)
            if entry is None or entry.object_type != "tree" or entry.mode != "040000":
                return _blobs(workflow_files, None)
            tree = await self._tree(client, repository, entry.object_sha)
        if isinstance(tree, ComponentReadFailure):
            return tree
        return _blobs(workflow_files, tree)

    async def _tree(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        tree_sha: str,
    ) -> GitTree | ComponentReadFailure:
        outcome = self._success(
            await client.get_tree(repository, tree_sha),
            operation="workflow_discovery.get_tree",
            path=f"{repository.path}/git/trees/{path_value(tree_sha)}",
            not_found="provider_binding_mismatch",
        )
        if isinstance(outcome, ComponentReadFailure):
            return outcome
        tree = decode_git_tree(
            outcome.response.body,
            expected_tree_sha=tree_sha,
            max_json_bytes=_MAX_JSON_BYTES,
            max_entries=_MAX_TREE_ENTRIES,
        )
        if tree is None:
            return ComponentReadFailure("malformed_provider_response")
        if tree.limit_exceeded:
            return ComponentReadFailure("provider_limit_exceeded")
        return tree

    def _success(
        self,
        outcome: GitHubOutcome,
        *,
        operation: str,
        path: str,
        not_found: ComponentReadFailureReason,
    ) -> GitHubSuccess | ComponentReadFailure:
        if isinstance(outcome, GitHubUnavailable):
            kind = outcome.failure.kind
            if kind == "rate_limited":
                return ComponentReadFailure("rate_limited")
            if kind == "not_found":
                return ComponentReadFailure(not_found)
            if kind in {"missing_api_version_provenance", "api_version_provenance_mismatch"}:
                return ComponentReadFailure("provider_binding_mismatch")
            return ComponentReadFailure("provider_unavailable")
        if not isinstance(outcome, GitHubSuccess) or not _request_matches(
            outcome.request, operation=operation, path=path, api_version=self._api_version
        ):
            return ComponentReadFailure("provider_binding_mismatch")
        if outcome.response.pagination.termination != "not_paginated":
            return ComponentReadFailure("malformed_provider_response")
        return outcome


def _location(
    found: dict[str, ComponentRepositoryScope],
    *,
    complete: bool,
) -> ComponentRepositoryLocation:
    return ComponentRepositoryLocation(
        tuple(found[key] for key in sorted(found, key=utf16_sort_key)),
        complete,
    )


def _blobs(
    workflow_files: tuple[str, ...],
    tree: GitTree | None,
) -> tuple[ComponentWorkflowBlob, ...]:
    entries = {} if tree is None else {entry.name: entry for entry in tree.entries}
    result: list[ComponentWorkflowBlob] = []
    for file_name in sorted(set(workflow_files), key=utf16_sort_key):
        entry = entries.get(file_name)
        regular = (
            entry is not None and entry.object_type == "blob" and entry.mode in {"100644", "100755"}
        )
        result.append(
            ComponentWorkflowBlob(
                file_name, entry.object_sha if entry is not None and regular else None
            )
        )
    return tuple(result)
