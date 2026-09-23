"""Fail-closed GitHub reader for workflow authority evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.integrations.github._routes import (
    GitHubRepository,
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
from ci_coordinator.integrations.github.workflow_authority_client import WorkflowAuthorityClient
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    DiscoveryRepository,
    GitBlob,
    GitCommit,
    GitTree,
    GitTreeEntry,
    decode_discovery_repository,
    decode_git_blob,
    decode_git_commit,
    decode_git_tree,
    is_github_object_id,
)
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.workflow_authority import (
    GitTreeChild,
    RetainedBlobObject,
    RetainedTreeObject,
    WorkflowAuthorityError,
    WorkflowAuthorityEvidence,
    WorkflowAuthorityFailureReason,
    WorkflowAuthorityReadOutcome,
    WorkflowAuthorityRepository,
    WorkflowAuthorityUnavailable,
    WorkflowCommitRequest,
    git_tree_sort_key,
)
from ci_coordinator.workflow_authority.limits import (
    MAX_BLOB_BYTES,
    MAX_COMMIT_RESPONSE_BYTES,
    MAX_EVIDENCE_BYTES,
    MAX_MANIFEST_ENTRIES,
    MAX_TREE_DEPTH,
    MAX_TREE_ENTRIES,
    MAX_TREE_OBJECTS,
)

_MAX_JSON_BYTES = 8_388_608
_MAX_CONCURRENT_BLOBS = 8
_REGULAR_MODES = frozenset({"100644", "100755"})
_WORKFLOWS_ROOT = ".github/workflows"


@dataclass(frozen=True, slots=True)
class _CommitEvidence:
    commit: GitCommit
    response_body: bytes
    request: WorkflowCommitRequest


@dataclass(frozen=True, slots=True)
class _BlobReference:
    path: str
    entry: GitTreeEntry


class GitHubWorkflowAuthorityReader:
    def __init__(
        self,
        transport_factory: GitHubAppTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("workflow authority API version must be non-empty")
        self._transport_factory = transport_factory
        self._api_version = api_version

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str,
    ) -> WorkflowAuthorityReadOutcome:
        if type(scope) is not RepositoryScope:
            raise TypeError("workflow authority reader requires an exact repository scope")
        if not is_github_object_id(revision):
            return WorkflowAuthorityUnavailable("invalid_revision")
        client = WorkflowAuthorityClient(
            self._transport_factory.for_installation(scope.installation_id),
            api_version=self._api_version,
        )
        try:
            return await self._read_bound(client=client, scope=scope, revision=revision)
        except WorkflowAuthorityError as error:
            return WorkflowAuthorityUnavailable(_evidence_failure_reason(error))
        except (TypeError, ValueError):
            return WorkflowAuthorityUnavailable("malformed_provider_response")

    async def _read_bound(
        self,
        *,
        client: WorkflowAuthorityClient,
        scope: RepositoryScope,
        revision: str,
    ) -> WorkflowAuthorityReadOutcome:
        repository = await self._repository(client, scope.repository_id)
        if isinstance(repository, WorkflowAuthorityUnavailable):
            return repository
        commit = await self._commit(client, repository.repository, revision)
        if isinstance(commit, WorkflowAuthorityUnavailable):
            return commit
        root = await self._tree(client, repository.repository, commit.commit.tree_sha)
        if isinstance(root, WorkflowAuthorityUnavailable):
            return root
        root_object = _retained_tree("", commit.commit.tree_sha, root)
        github_entry = _entry(root, ".github")
        if github_entry is None:
            return WorkflowAuthorityUnavailable("source_tree_missing")
        if github_entry.object_type != "tree" or github_entry.mode != "040000":
            return WorkflowAuthorityUnavailable("unsupported_object")
        github = await self._tree(client, repository.repository, github_entry.object_sha)
        if isinstance(github, WorkflowAuthorityUnavailable):
            return github
        github_object = _retained_tree(".github", github_entry.object_sha, github)
        workflows_entry = _entry(github, "workflows")
        if workflows_entry is None:
            return WorkflowAuthorityUnavailable("source_tree_missing")
        if workflows_entry.object_type != "tree" or workflows_entry.mode != "040000":
            return WorkflowAuthorityUnavailable("unsupported_object")
        closure = await self._workflow_closure(
            client,
            repository.repository,
            workflows_entry.object_sha,
        )
        if isinstance(closure, WorkflowAuthorityUnavailable):
            return closure
        workflow_trees, blob_references = closure
        blobs = await self._blobs(client, repository.repository, blob_references)
        if isinstance(blobs, WorkflowAuthorityUnavailable):
            return blobs
        rebound = await self._repository(client, scope.repository_id)
        if rebound != repository:
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        trees = tuple(
            sorted(
                (root_object, github_object, *workflow_trees),
                key=lambda item: utf16_sort_key(item.path),
            )
        )
        return WorkflowAuthorityEvidence.create(
            repository=WorkflowAuthorityRepository(
                scope=scope,
                owner=repository.repository.owner,
                name=repository.repository.name,
                default_branch=repository.default_branch,
            ),
            provider_request=commit.request,
            source_commit_id=revision,
            root_tree_id=commit.commit.tree_sha,
            commit_response=commit.response_body,
            trees=trees,
            blobs=blobs,
        )

    async def _workflow_closure(
        self,
        client: WorkflowAuthorityClient,
        repository: GitHubRepository,
        workflows_tree_id: str,
    ) -> (
        tuple[tuple[RetainedTreeObject, ...], tuple[_BlobReference, ...]]
        | WorkflowAuthorityUnavailable
    ):
        pending = [(_WORKFLOWS_ROOT, workflows_tree_id, 0)]
        trees: list[RetainedTreeObject] = []
        blobs: list[_BlobReference] = []
        seen_paths: set[str] = set()
        total_entries = 0
        while pending:
            path, tree_id, depth = pending.pop()
            if path in seen_paths:
                return WorkflowAuthorityUnavailable("malformed_provider_response")
            seen_paths.add(path)
            if depth > MAX_TREE_DEPTH or len(seen_paths) > MAX_TREE_OBJECTS - 2:
                return WorkflowAuthorityUnavailable("source_limit_exceeded")
            tree = await self._tree(client, repository, tree_id)
            if isinstance(tree, WorkflowAuthorityUnavailable):
                return tree
            trees.append(_retained_tree(path, tree_id, tree))
            total_entries += len(tree.entries)
            if total_entries > MAX_MANIFEST_ENTRIES:
                return WorkflowAuthorityUnavailable("source_limit_exceeded")
            for entry in tree.entries:
                child_path = f"{path}/{entry.name}"
                if entry.object_type == "tree" and entry.mode == "040000":
                    pending.append((child_path, entry.object_sha, depth + 1))
                elif entry.object_type == "blob" and entry.mode in _REGULAR_MODES:
                    if entry.size is None or entry.size > MAX_BLOB_BYTES:
                        return WorkflowAuthorityUnavailable("source_limit_exceeded")
                    blobs.append(_BlobReference(child_path, entry))
                else:
                    return WorkflowAuthorityUnavailable("unsupported_object")
        if sum(reference.entry.size or 0 for reference in blobs) > MAX_EVIDENCE_BYTES:
            return WorkflowAuthorityUnavailable("source_limit_exceeded")
        return (
            tuple(sorted(trees, key=lambda item: utf16_sort_key(item.path))),
            tuple(sorted(blobs, key=lambda item: utf16_sort_key(item.path))),
        )

    async def _blobs(
        self,
        client: WorkflowAuthorityClient,
        repository: GitHubRepository,
        references: tuple[_BlobReference, ...],
    ) -> tuple[RetainedBlobObject, ...] | WorkflowAuthorityUnavailable:
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BLOBS)
        results = await asyncio.gather(
            *(self._blob(client, repository, reference, semaphore) for reference in references)
        )
        failure = next(
            (result for result in results if isinstance(result, WorkflowAuthorityUnavailable)),
            None,
        )
        if failure is not None:
            return failure
        return tuple(result for result in results if isinstance(result, RetainedBlobObject))

    async def _blob(
        self,
        client: WorkflowAuthorityClient,
        repository: GitHubRepository,
        reference: _BlobReference,
        semaphore: asyncio.Semaphore,
    ) -> RetainedBlobObject | WorkflowAuthorityUnavailable:
        entry = reference.entry
        if entry.size is None:
            return WorkflowAuthorityUnavailable("malformed_provider_response")
        async with semaphore:
            outcome = await client.get_blob(repository, entry.object_sha)
        path = f"{repository.path}/git/blobs/{path_value(entry.object_sha)}"
        failure = _outcome_failure(
            outcome,
            operation="workflow_authority.get_blob",
            path=path,
            api_version=self._api_version,
        )
        if failure is not None:
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        blob = decode_git_blob(
            outcome.response.body,
            expected_blob_sha=entry.object_sha,
            expected_size=entry.size,
            max_json_bytes=_MAX_JSON_BYTES,
            max_content_bytes=MAX_BLOB_BYTES,
        )
        if not isinstance(blob, GitBlob):
            return WorkflowAuthorityUnavailable("malformed_provider_response")
        try:
            return RetainedBlobObject(
                reference.path,
                entry.mode,  # type: ignore[arg-type]
                entry.object_sha,
                entry.size,
                blob.content,
            )
        except (TypeError, ValueError):
            return WorkflowAuthorityUnavailable("malformed_provider_response")

    async def _repository(
        self,
        client: WorkflowAuthorityClient,
        repository_id: int,
    ) -> DiscoveryRepository | WorkflowAuthorityUnavailable:
        outcome = await client.get_repository(repository_id)
        path = repository_id_path(repository_id)
        failure = _outcome_failure(
            outcome,
            operation="workflow_authority.get_repository",
            path=path,
            api_version=self._api_version,
        )
        if failure is not None:
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        repository = decode_discovery_repository(
            outcome.response.body,
            max_json_bytes=_MAX_JSON_BYTES,
        )
        if repository is None:
            return WorkflowAuthorityUnavailable("malformed_provider_response")
        if repository.repository_id != repository_id:
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        return repository

    async def _commit(
        self,
        client: WorkflowAuthorityClient,
        repository: GitHubRepository,
        revision: str,
    ) -> _CommitEvidence | WorkflowAuthorityUnavailable:
        outcome = await client.get_commit(repository, revision)
        path = f"{repository.path}/git/commits/{path_value(revision)}"
        failure = _outcome_failure(
            outcome,
            operation="workflow_authority.get_commit",
            path=path,
            api_version=self._api_version,
        )
        if failure is not None:
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        body = outcome.response.body
        if len(body) > MAX_COMMIT_RESPONSE_BYTES:
            return WorkflowAuthorityUnavailable("source_limit_exceeded")
        commit = decode_git_commit(
            body,
            expected_commit_sha=revision,
            max_json_bytes=MAX_COMMIT_RESPONSE_BYTES,
        )
        if commit is None:
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        request = outcome.request
        if request.api_version is None:
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        try:
            retained_request = WorkflowCommitRequest(
                operation=request.operation,  # type: ignore[arg-type]
                method=request.method,  # type: ignore[arg-type]
                path=request.path,
                api_version=request.api_version,
                query=tuple((item.name, item.value) for item in request.query),
                body_absent=request.body is None,
            )
        except (TypeError, ValueError):
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        return _CommitEvidence(commit, body, retained_request)

    async def _tree(
        self,
        client: WorkflowAuthorityClient,
        repository: GitHubRepository,
        tree_id: str,
    ) -> GitTree | WorkflowAuthorityUnavailable:
        outcome = await client.get_tree(repository, tree_id)
        path = f"{repository.path}/git/trees/{path_value(tree_id)}"
        failure = _outcome_failure(
            outcome,
            operation="workflow_authority.get_tree",
            path=path,
            api_version=self._api_version,
        )
        if failure is not None:
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        tree = decode_git_tree(
            outcome.response.body,
            expected_tree_sha=tree_id,
            max_json_bytes=_MAX_JSON_BYTES,
            max_entries=MAX_TREE_ENTRIES,
        )
        if tree is None:
            return WorkflowAuthorityUnavailable("malformed_provider_response")
        if tree.limit_exceeded:
            return WorkflowAuthorityUnavailable("source_limit_exceeded")
        return tree


def _retained_tree(path: str, object_id: str, tree: GitTree) -> RetainedTreeObject:
    entries = tuple(
        sorted(
            (
                GitTreeChild(
                    entry.name,
                    entry.mode,
                    entry.object_type,  # type: ignore[arg-type]
                    entry.object_sha,
                    entry.size,
                )
                for entry in tree.entries
            ),
            key=lambda item: git_tree_sort_key(item),
        )
    )
    return RetainedTreeObject(path, object_id, entries)


def _entry(tree: GitTree, name: str) -> GitTreeEntry | None:
    return next((entry for entry in tree.entries if entry.name == name), None)


def _outcome_failure(
    outcome: GitHubOutcome,
    *,
    operation: str,
    path: str,
    api_version: str,
) -> WorkflowAuthorityUnavailable | None:
    if isinstance(outcome, GitHubUnavailable):
        kind = outcome.failure.kind
        if kind == "rate_limited":
            return WorkflowAuthorityUnavailable("rate_limited")
        if kind == "not_found":
            return WorkflowAuthorityUnavailable("not_found")
        if kind in {"missing_api_version_provenance", "api_version_provenance_mismatch"}:
            return WorkflowAuthorityUnavailable("provider_binding_mismatch")
        return WorkflowAuthorityUnavailable("unavailable")
    if not isinstance(outcome, GitHubSuccess):
        return WorkflowAuthorityUnavailable("provider_binding_mismatch")
    request = outcome.request
    if (
        request.operation != operation
        or request.method != "GET"
        or request.path != path
        or request.api_version != api_version
        or request.query != ()
        or request.body is not None
        or outcome.response.pagination.termination != "not_paginated"
    ):
        return WorkflowAuthorityUnavailable("provider_binding_mismatch")
    return None


def _evidence_failure_reason(error: WorkflowAuthorityError) -> WorkflowAuthorityFailureReason:
    if error.code == "unsupported_workflow_object":
        return "unsupported_object"
    if error.code in {"tree_depth_exceeded", "manifest_entry_limit_exceeded"}:
        return "source_limit_exceeded"
    return "provider_binding_mismatch"
