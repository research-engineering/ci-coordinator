"""Exact-commit Git database adapter for workflow discovery."""

from __future__ import annotations

import asyncio
from typing import Final, Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration import (
    MAX_TARGET_EXECUTION_REGISTRY_BYTES,
    TARGET_EXECUTION_REGISTRY_PATH,
    TargetExecutionRegistry,
    parse_target_execution_registry,
)
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
from ci_coordinator.integrations.github.request_admission import (
    get_request_matches as _request_matches,
)
from ci_coordinator.integrations.github.workflow_discovery_client import WorkflowDiscoveryClient
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    DiscoveryRepository,
    GitBlob,
    GitCommit,
    GitTree,
    GitTreeEntry,
    decode_discovery_repository,
    decode_git_blob,
    decode_git_commit,
    decode_git_reference,
    decode_git_tree,
    is_github_object_id,
)
from ci_coordinator.kernel import NoQueueAdmission
from ci_coordinator.workflow_discovery.adoption_target import (
    AdoptionTargetJob,
    AdoptionTargetProjection,
    AdoptionTargetWorkflow,
)
from ci_coordinator.workflow_discovery.outcomes import (
    SnapshotReadOutcome,
    WorkflowDiscoveryUnavailable,
)
from ci_coordinator.workflow_discovery.source import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowSource,
    WorkflowSourceFailure,
    WorkflowSourceFailureReason,
)

_MAX_JSON_BYTES = 1_048_576
_MAX_TREE_ENTRIES = 4_096
_MAX_WORKFLOW_FILES = 64
_MAX_WORKFLOW_FILE_BYTES = 262_144
_MAX_WORKFLOW_AGGREGATE_BYTES = 4_194_304
WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_BLOBS: Final = 8
WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_SNAPSHOTS: Final = 1


class GitHubWorkflowSnapshotReader:
    def __init__(
        self,
        transport_factory: GitHubAppTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("workflow discovery API version must be non-empty")
        self._transport_factory = transport_factory
        self._api_version = api_version
        self._snapshot_admission = NoQueueAdmission(WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_SNAPSHOTS)

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> SnapshotReadOutcome:
        if revision is not None and not is_github_object_id(revision):
            return WorkflowDiscoveryUnavailable("invalid_revision")
        lease = self._snapshot_admission.try_acquire()
        if lease is None:
            return WorkflowDiscoveryUnavailable("overloaded")
        try:
            client = WorkflowDiscoveryClient(
                self._transport_factory.for_installation(scope.installation_id),
                api_version=self._api_version,
            )
            repository = await self._repository(client, scope.repository_id)
            if isinstance(repository, WorkflowDiscoveryUnavailable):
                return repository
            if revision is None:
                resolved_revision = await self._default_revision(client, repository)
                if isinstance(resolved_revision, WorkflowDiscoveryUnavailable):
                    return resolved_revision
                exact_revision = resolved_revision
            else:
                exact_revision = revision
            commit = await self._commit(client, repository.repository, exact_revision)
            if isinstance(commit, WorkflowDiscoveryUnavailable):
                return commit
            root = await self._tree(client, repository.repository, commit.tree_sha)
            if isinstance(root, WorkflowDiscoveryUnavailable):
                return root
            candidate_result, target_projection = await asyncio.gather(
                self._workflow_entries(client, repository.repository, root),
                self._adoption_target_projection(client, repository.repository, root),
            )
            if isinstance(candidate_result, WorkflowDiscoveryUnavailable):
                return candidate_result
            entries, structural_failures = candidate_result
            semaphore = asyncio.Semaphore(WORKFLOW_DISCOVERY_MAXIMUM_CONCURRENT_BLOBS)
            fetched = await asyncio.gather(
                *(self._blob(client, repository.repository, entry, semaphore) for entry in entries)
            )
            sources = tuple(item for item in fetched if isinstance(item, WorkflowSource))
            failures = (
                *structural_failures,
                *(item for item in fetched if isinstance(item, WorkflowSourceFailure)),
            )
            rebound = await self._repository(client, scope.repository_id)
            if rebound != repository:
                return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
            return RepositoryWorkflowSnapshot.create(
                repository=RepositoryIdentity(
                    scope,
                    repository.repository.owner,
                    repository.repository.name,
                    repository.default_branch,
                ),
                revision=exact_revision,
                sources=sources,
                failures=failures,
                target_projection=target_projection,
            )
        finally:
            lease.release()

    async def _repository(
        self,
        client: WorkflowDiscoveryClient,
        repository_id: int,
    ) -> DiscoveryRepository | WorkflowDiscoveryUnavailable:
        outcome = await client.get_repository(repository_id)
        expected_path = repository_id_path(repository_id)
        if failure := _outcome_failure(
            outcome,
            operation="workflow_discovery.get_repository",
            path=expected_path,
            api_version=self._api_version,
        ):
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        repository = decode_discovery_repository(
            outcome.response.body,
            max_json_bytes=_MAX_JSON_BYTES,
        )
        if repository is None:
            return WorkflowDiscoveryUnavailable("malformed_provider_response")
        if repository.repository_id != repository_id:
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        return repository

    async def _default_revision(
        self,
        client: WorkflowDiscoveryClient,
        repository: DiscoveryRepository,
    ) -> str | WorkflowDiscoveryUnavailable:
        ref = f"heads/{repository.default_branch}"
        outcome = await client.get_reference(repository.repository, ref)
        path = f"{repository.repository.path}/git/ref/{contents_path_value(ref)}"
        if failure := _outcome_failure(
            outcome,
            operation="workflow_discovery.get_reference",
            path=path,
            api_version=self._api_version,
        ):
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        revision = decode_git_reference(
            outcome.response.body,
            expected_ref=ref,
            max_json_bytes=_MAX_JSON_BYTES,
        )
        return revision or WorkflowDiscoveryUnavailable("malformed_provider_response")

    async def _commit(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        revision: str,
    ) -> GitCommit | WorkflowDiscoveryUnavailable:
        outcome = await client.get_commit(repository, revision)
        path = f"{repository.path}/git/commits/{path_value(revision)}"
        if failure := _outcome_failure(
            outcome,
            operation="workflow_discovery.get_commit",
            path=path,
            api_version=self._api_version,
        ):
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        commit = decode_git_commit(
            outcome.response.body,
            expected_commit_sha=revision,
            max_json_bytes=_MAX_JSON_BYTES,
        )
        return commit or WorkflowDiscoveryUnavailable("provider_binding_mismatch")

    async def _tree(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        tree_sha: str,
    ) -> GitTree | WorkflowDiscoveryUnavailable:
        outcome = await client.get_tree(repository, tree_sha)
        path = f"{repository.path}/git/trees/{path_value(tree_sha)}"
        if failure := _outcome_failure(
            outcome,
            operation="workflow_discovery.get_tree",
            path=path,
            api_version=self._api_version,
        ):
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        tree = decode_git_tree(
            outcome.response.body,
            expected_tree_sha=tree_sha,
            max_json_bytes=_MAX_JSON_BYTES,
            max_entries=_MAX_TREE_ENTRIES,
        )
        if tree is None:
            return WorkflowDiscoveryUnavailable("malformed_provider_response")
        if tree.limit_exceeded:
            return WorkflowDiscoveryUnavailable("source_tree_limit_exceeded")
        return tree

    async def _workflow_entries(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        root: GitTree,
    ) -> (
        tuple[tuple[GitTreeEntry, ...], tuple[WorkflowSourceFailure, ...]]
        | WorkflowDiscoveryUnavailable
    ):
        github = _entry(root, ".github")
        if github is None:
            return (), ()
        if github.object_type != "tree" or github.mode != "040000":
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        metadata = await self._tree(client, repository, github.object_sha)
        if isinstance(metadata, WorkflowDiscoveryUnavailable):
            return metadata
        workflows = _entry(metadata, "workflows")
        if workflows is None:
            return (), ()
        if workflows.object_type != "tree" or workflows.mode != "040000":
            return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
        workflow_tree = await self._tree(client, repository, workflows.object_sha)
        if isinstance(workflow_tree, WorkflowDiscoveryUnavailable):
            return workflow_tree
        return _classify_workflow_entries(workflow_tree)

    async def _blob(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        entry: GitTreeEntry,
        semaphore: asyncio.Semaphore,
    ) -> WorkflowSource | WorkflowSourceFailure:
        if entry.size is None:
            raise TypeError("admitted workflow blob entry requires a declared size")
        path = f".github/workflows/{entry.name}"
        async with semaphore:
            outcome = await client.get_blob(repository, entry.object_sha)
        expected_path = f"{repository.path}/git/blobs/{path_value(entry.object_sha)}"
        if failure := _source_outcome_failure(
            outcome,
            path=path,
            blob_sha=entry.object_sha,
            size=entry.size,
            expected_path=expected_path,
            api_version=self._api_version,
        ):
            return failure
        if not isinstance(outcome, GitHubSuccess):
            return WorkflowSourceFailure(
                path,
                entry.object_sha,
                entry.size,
                "provider_binding_mismatch",
            )
        blob = decode_git_blob(
            outcome.response.body,
            expected_blob_sha=entry.object_sha,
            expected_size=entry.size,
            max_json_bytes=_MAX_JSON_BYTES,
            max_content_bytes=_MAX_WORKFLOW_FILE_BYTES,
        )
        if not isinstance(blob, GitBlob):
            return WorkflowSourceFailure(
                path,
                entry.object_sha,
                entry.size,
                "provider_binding_mismatch",
            )
        return WorkflowSource(path, blob.blob_sha, entry.size, blob.content)

    async def _adoption_target_projection(
        self,
        client: WorkflowDiscoveryClient,
        repository: GitHubRepository,
        root: GitTree,
    ) -> AdoptionTargetProjection:
        directory = _entry(root, ".ci-coordinator")
        if directory is None:
            return AdoptionTargetProjection.absent()
        if directory.object_type != "tree" or directory.mode != "040000":
            return AdoptionTargetProjection.failed("invalid")
        tree = await self._tree(client, repository, directory.object_sha)
        if isinstance(tree, WorkflowDiscoveryUnavailable):
            return AdoptionTargetProjection.failed(_target_projection_failure_status(tree))
        entry = _entry(tree, TARGET_EXECUTION_REGISTRY_PATH.rsplit("/", 1)[1])
        if entry is None:
            return AdoptionTargetProjection.absent()
        if (
            entry.object_type != "blob"
            or entry.mode not in {"100644", "100755"}
            or entry.size is None
            or entry.size > MAX_TARGET_EXECUTION_REGISTRY_BYTES
        ):
            return AdoptionTargetProjection.failed("invalid")
        outcome = await client.get_blob(repository, entry.object_sha)
        expected_path = f"{repository.path}/git/blobs/{path_value(entry.object_sha)}"
        if failure := _outcome_failure(
            outcome,
            operation="workflow_discovery.get_blob",
            path=expected_path,
            api_version=self._api_version,
        ):
            return AdoptionTargetProjection.failed(_target_projection_failure_status(failure))
        if not isinstance(outcome, GitHubSuccess):
            return AdoptionTargetProjection.failed("invalid")
        blob = decode_git_blob(
            outcome.response.body,
            expected_blob_sha=entry.object_sha,
            expected_size=entry.size,
            max_json_bytes=_MAX_JSON_BYTES,
            max_content_bytes=MAX_TARGET_EXECUTION_REGISTRY_BYTES,
        )
        if not isinstance(blob, GitBlob):
            return AdoptionTargetProjection.failed("invalid")
        registry = parse_target_execution_registry(blob.content)
        if registry is None:
            return AdoptionTargetProjection.failed("invalid")
        return _project_adoption_target(registry)


def _entry(tree: GitTree, name: str) -> GitTreeEntry | None:
    return next((entry for entry in tree.entries if entry.name == name), None)


def _project_adoption_target(
    registry: TargetExecutionRegistry,
) -> AdoptionTargetProjection:
    return AdoptionTargetProjection.available(
        registry.registry_hash,
        tuple(
            AdoptionTargetWorkflow(
                workflow_path=workflow.workflow_path,
                execution_kind=workflow.execution_kind,
                execution_jobs=tuple(
                    AdoptionTargetJob(job.job_id, job.needs) for job in workflow.execution_jobs
                ),
                invocation_job_id=workflow.invocation_job_id,
                plan_request_job_id=workflow.plan_request_job_id,
                plan_job_id=workflow.plan_job_id,
                fallback_job_id=workflow.fallback_job_id,
                gate_job_id=workflow.gate_job_id,
                gate_signal_name=workflow.gate_signal_name,
            )
            for workflow in registry.workflows
        ),
    )


def _target_projection_failure_status(
    failure: WorkflowDiscoveryUnavailable,
) -> Literal["invalid", "unavailable"]:
    return (
        "unavailable"
        if failure.reason in {"not_found", "rate_limited", "unavailable"}
        else "invalid"
    )


def _classify_workflow_entries(
    tree: GitTree,
) -> (
    tuple[tuple[GitTreeEntry, ...], tuple[WorkflowSourceFailure, ...]]
    | WorkflowDiscoveryUnavailable
):
    matching = tuple(
        entry
        for entry in tree.entries
        if entry.object_type == "blob" and entry.name.endswith((".yml", ".yaml"))
    )
    if len(matching) > _MAX_WORKFLOW_FILES:
        return WorkflowDiscoveryUnavailable("source_limit_exceeded")
    sized: list[tuple[GitTreeEntry, int]] = []
    for entry in matching:
        if entry.size is None:
            return WorkflowDiscoveryUnavailable("malformed_provider_response")
        sized.append((entry, entry.size))
    if (
        any(size > _MAX_WORKFLOW_FILE_BYTES for _, size in sized)
        or sum(size for _, size in sized) > _MAX_WORKFLOW_AGGREGATE_BYTES
    ):
        return WorkflowDiscoveryUnavailable("source_limit_exceeded")

    regular: list[GitTreeEntry] = []
    failures: list[WorkflowSourceFailure] = []
    for entry, size in sized:
        if entry.mode in {"100644", "100755"}:
            regular.append(entry)
        else:
            failures.append(
                WorkflowSourceFailure(
                    f".github/workflows/{entry.name}",
                    entry.object_sha,
                    size,
                    "unsupported_object",
                )
            )
    return tuple(regular), tuple(failures)


def _outcome_failure(
    outcome: GitHubOutcome,
    *,
    operation: str,
    path: str,
    api_version: str,
) -> WorkflowDiscoveryUnavailable | None:
    if isinstance(outcome, GitHubUnavailable):
        return _unavailable(outcome)
    if not isinstance(outcome, GitHubSuccess) or not _request_matches(
        outcome.request, operation=operation, path=path, api_version=api_version
    ):
        return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
    if outcome.response.pagination.termination != "not_paginated":
        return WorkflowDiscoveryUnavailable("malformed_provider_response")
    return None


def _source_outcome_failure(
    outcome: GitHubOutcome,
    *,
    path: str,
    blob_sha: str,
    size: int,
    expected_path: str,
    api_version: str,
) -> WorkflowSourceFailure | None:
    if isinstance(outcome, GitHubUnavailable):
        reason = _source_failure_reason(outcome)
        return WorkflowSourceFailure(path, blob_sha, size, reason)
    if not isinstance(outcome, GitHubSuccess) or not _request_matches(
        outcome.request,
        operation="workflow_discovery.get_blob",
        path=expected_path,
        api_version=api_version,
    ):
        return WorkflowSourceFailure(path, blob_sha, size, "provider_binding_mismatch")
    if outcome.response.pagination.termination != "not_paginated":
        return WorkflowSourceFailure(path, blob_sha, size, "malformed_provider_response")
    return None


def _unavailable(outcome: GitHubUnavailable) -> WorkflowDiscoveryUnavailable:
    kind = outcome.failure.kind
    if kind == "rate_limited":
        return WorkflowDiscoveryUnavailable("rate_limited")
    if kind == "not_found":
        return WorkflowDiscoveryUnavailable("not_found")
    if kind in {"missing_api_version_provenance", "api_version_provenance_mismatch"}:
        return WorkflowDiscoveryUnavailable("provider_binding_mismatch")
    return WorkflowDiscoveryUnavailable("unavailable")


def _source_failure_reason(outcome: GitHubUnavailable) -> WorkflowSourceFailureReason:
    kind = outcome.failure.kind
    if kind == "rate_limited":
        return "rate_limited"
    if kind == "not_found":
        return "not_found"
    if kind in {"missing_api_version_provenance", "api_version_provenance_mismatch"}:
        return "provider_binding_mismatch"
    return "unavailable"
