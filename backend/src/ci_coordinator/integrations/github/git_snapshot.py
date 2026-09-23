"""Bounded Git Data reads for regular files at one exact repository revision."""

from __future__ import annotations

from typing import Final, TypeGuard

from ci_coordinator.integrations.github._routes import GitHubRepository, path_value
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.recursive_git_tree import decode_recursive_git_tree
from ci_coordinator.integrations.github.workflow_discovery_client import (
    WorkflowDiscoveryClient,
)
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    GitBlob,
    GitCommit,
    GitTree,
    GitTreeEntry,
    decode_git_blob,
    decode_git_commit,
    decode_git_tree,
    is_github_object_id,
)

_MAX_JSON_BYTES: Final = 2_097_152
_MAX_TREE_ENTRIES: Final = 4_096
_MAX_PATH_BYTES: Final = 1_024
_MAX_PATH_DEPTH: Final = 16
_REGULAR_BLOB_MODES: Final = frozenset({"100644", "100755"})


class GitHubGitSnapshotReader:
    """Read bounded regular blobs without Contents API symlink dereferencing."""

    def __init__(self, client: WorkflowDiscoveryClient) -> None:
        if type(client) is not WorkflowDiscoveryClient:
            raise TypeError("Git snapshot reader requires an exact Git client")
        self._client = client

    async def root_tree(
        self,
        repository: GitHubRepository,
        revision_sha: str,
    ) -> GitTree | None:
        if type(repository) is not GitHubRepository:
            raise TypeError("Git snapshot reader requires an exact repository")
        if not is_github_object_id(revision_sha):
            raise ValueError("Git snapshot reader requires an immutable revision")
        commit = await self._commit(repository, revision_sha)
        return None if commit is None else await self.tree(repository, commit.tree_sha)

    async def recursive_tree(
        self,
        repository: GitHubRepository,
        revision_sha: str,
        *,
        maximum_entries: int,
    ) -> dict[str, GitTree] | None:
        if type(repository) is not GitHubRepository:
            raise TypeError("Git snapshot reader requires an exact repository")
        if not is_github_object_id(revision_sha):
            raise ValueError("Git snapshot reader requires an immutable revision")
        commit = await self._commit(repository, revision_sha)
        if commit is None:
            return None
        outcome = await self._client.get_recursive_tree(repository, commit.tree_sha)
        if not _successful_request(
            outcome,
            operation="workflow_discovery.get_recursive_tree",
            path=f"{repository.path}/git/trees/{path_value(commit.tree_sha)}",
            query=(GitHubQueryParameter("recursive", "1"),),
        ):
            return None
        if outcome.response.pagination != GitHubPaginationEvidence.not_paginated():
            return None
        return decode_recursive_git_tree(
            outcome.response.body,
            expected_tree_sha=commit.tree_sha,
            maximum_entries=maximum_entries,
        )

    async def regular_file(
        self,
        repository: GitHubRepository,
        *,
        revision_sha: str,
        path: str,
        maximum_bytes: int,
    ) -> bytes | None:
        parts = _path_parts(path)
        if parts is None or type(maximum_bytes) is not int or maximum_bytes < 0:
            raise ValueError("regular Git file request is outside its bounded profile")
        tree = await self.root_tree(repository, revision_sha)
        if tree is None:
            return None
        for directory in parts[:-1]:
            tree = await self.child_tree(repository, tree, directory)
            if tree is None:
                return None
        entry = regular_blob_entry(
            tree,
            parts[-1],
            maximum_bytes=maximum_bytes,
        )
        return (
            None
            if entry is None
            else await self.blob(repository, entry, maximum_bytes=maximum_bytes)
        )

    async def child_tree(
        self,
        repository: GitHubRepository,
        parent: GitTree,
        name: str,
    ) -> GitTree | None:
        entry = tree_entry(parent, name)
        if entry is None or entry.object_type != "tree" or entry.mode != "040000":
            return None
        return await self.tree(repository, entry.object_sha)

    async def tree(
        self,
        repository: GitHubRepository,
        tree_sha: str,
    ) -> GitTree | None:
        outcome = await self._client.get_tree(repository, tree_sha)
        if not _successful_request(
            outcome,
            operation="workflow_discovery.get_tree",
            path=f"{repository.path}/git/trees/{path_value(tree_sha)}",
        ):
            return None
        tree = decode_git_tree(
            outcome.response.body,
            expected_tree_sha=tree_sha,
            max_json_bytes=_MAX_JSON_BYTES,
            max_entries=_MAX_TREE_ENTRIES,
        )
        return None if tree is None or tree.limit_exceeded else tree

    async def blob(
        self,
        repository: GitHubRepository,
        entry: GitTreeEntry,
        *,
        maximum_bytes: int,
    ) -> bytes | None:
        if entry.size is None:
            return None
        outcome = await self._client.get_blob(repository, entry.object_sha)
        if not _successful_request(
            outcome,
            operation="workflow_discovery.get_blob",
            path=f"{repository.path}/git/blobs/{path_value(entry.object_sha)}",
        ):
            return None
        blob = decode_git_blob(
            outcome.response.body,
            expected_blob_sha=entry.object_sha,
            expected_size=entry.size,
            max_json_bytes=_MAX_JSON_BYTES,
            max_content_bytes=maximum_bytes,
        )
        return blob.content if isinstance(blob, GitBlob) else None

    async def _commit(
        self,
        repository: GitHubRepository,
        revision_sha: str,
    ) -> GitCommit | None:
        outcome = await self._client.get_commit(repository, revision_sha)
        if not _successful_request(
            outcome,
            operation="workflow_discovery.get_commit",
            path=f"{repository.path}/git/commits/{path_value(revision_sha)}",
        ):
            return None
        return decode_git_commit(
            outcome.response.body,
            expected_commit_sha=revision_sha,
            max_json_bytes=_MAX_JSON_BYTES,
        )


def tree_entry(tree: GitTree, name: str) -> GitTreeEntry | None:
    return next((entry for entry in tree.entries if entry.name == name), None)


def regular_blob_entry(
    tree: GitTree,
    name: str,
    *,
    maximum_bytes: int,
) -> GitTreeEntry | None:
    entry = tree_entry(tree, name)
    if (
        entry is None
        or entry.object_type != "blob"
        or entry.mode not in _REGULAR_BLOB_MODES
        or entry.size is None
        or entry.size > maximum_bytes
    ):
        return None
    return entry


def _successful_request(
    outcome: object,
    *,
    operation: str,
    path: str,
    query: tuple[GitHubQueryParameter, ...] = (),
) -> TypeGuard[GitHubSuccess]:
    return (
        isinstance(outcome, GitHubSuccess)
        and outcome.request.operation == operation
        and outcome.request.method == "GET"
        and outcome.request.path == path
        and outcome.request.api_version == GITHUB_API_VERSION
        and outcome.request.query == query
        and outcome.request.body is None
    )


def _path_parts(path: object) -> tuple[str, ...] | None:
    if (
        type(path) is not str
        or not path
        or len(path.encode("utf-8")) > _MAX_PATH_BYTES
        or "\\" in path
    ):
        return None
    parts = tuple(path.split("/"))
    if len(parts) > _MAX_PATH_DEPTH or any(
        not part or part in {".", ".."} or len(part.encode("utf-8")) > 256 for part in parts
    ):
        return None
    return parts
