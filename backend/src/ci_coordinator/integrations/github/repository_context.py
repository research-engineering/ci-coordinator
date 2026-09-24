"""GitHub-backed, fail-closed repository-context provider."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from ci_coordinator.config_control.planning_projection import DynamicCiPlanningProjection
from ci_coordinator.integrations.github._routes import (
    GitHubRepository,
    contents_path_value,
    repository_id_path,
)
from ci_coordinator.integrations.github.actions_client import ActionsClient
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubSuccess,
)
from ci_coordinator.integrations.github.diff_client import DiffClient
from ci_coordinator.integrations.github.prepared_context import PreparedContextCache
from ci_coordinator.integrations.github.reconciliation_observer_decoding import decode_repository
from ci_coordinator.integrations.github.repository_context_decoding import (
    decode_contents_file,
)
from ci_coordinator.integrations.github.repository_context_diff import (
    current_pull_request_matches,
    invalid_diff,
    load_diff,
)
from ci_coordinator.integrations.github.self_ci_inventory import (
    SELF_CI_GENERATOR,
    self_ci_inventory_is_current,
)
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.integrations.github.workflow_catalog_client import WorkflowCatalogClient
from ci_coordinator.integrations.github.workflow_discovery_client import WorkflowDiscoveryClient
from ci_coordinator.kernel import MonotonicClock, SystemMonotonicClock
from ci_coordinator.plan_issuance.model import PlanRequest
from ci_coordinator.repo_context.dependency_graph import (
    DependencyGraphArtifact,
    DependencyGraphContext,
    GraphProvenance,
    build_dependency_graph,
)
from ci_coordinator.repo_context.dependency_graph_codec import (
    MAX_DEPENDENCY_GRAPH_BYTES,
    parse_dependency_graph_artifact,
)
from ci_coordinator.repo_context.diff_model import DiffContext, RepositoryEpoch
from ci_coordinator.repo_context.planning_input import (
    PlanningInput,
    PolicySnapshot,
    build_planning_input,
)
from ci_coordinator.repo_context.planning_preparation import ContextPreparationOutcome

DEPENDENCY_GRAPH_PATH = ".ci-coordinator/dependency-graph.v1.json"


@dataclass(frozen=True, slots=True)
class RepositoryContextLimits:
    max_diff_files: int = 1_000
    max_diff_pages: int = 20
    max_response_json_bytes: int = 1_048_576
    max_content_response_json_bytes: int = 2_097_152
    max_content_bytes: int = MAX_DEPENDENCY_GRAPH_BYTES

    def __post_init__(self) -> None:
        for value in (
            self.max_diff_files,
            self.max_diff_pages,
            self.max_response_json_bytes,
            self.max_content_response_json_bytes,
            self.max_content_bytes,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("repository context limits must be positive integers")


DEFAULT_REPOSITORY_CONTEXT_LIMITS = RepositoryContextLimits()


@dataclass(frozen=True, slots=True)
class GitHubRepositoryContext:
    repo_epoch: RepositoryEpoch
    diff: DiffContext
    dependency_graph: DependencyGraphContext
    planning_input: PlanningInput


class GitHubRepositoryContextProvider:
    """Adapt installation-bound GitHub protocol outcomes into planning facts."""

    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        api_version: str = GITHUB_API_VERSION,
        limits: RepositoryContextLimits = DEFAULT_REPOSITORY_CONTEXT_LIMITS,
        clock: MonotonicClock | None = None,
        observe: Callable[[str], None] | None = None,
    ) -> None:
        if type(api_version) is not str or not api_version:
            raise ValueError("GitHub API version provenance must be non-empty")
        self._transport_factory = transport_factory
        self._api_version = api_version
        self._limits = limits
        self._cache = PreparedContextCache(
            SystemMonotonicClock() if clock is None else clock, observe
        )
        self._observe = observe

    async def load(
        self,
        request: PlanRequest,
        projection: DynamicCiPlanningProjection | PolicySnapshot,
    ) -> GitHubRepositoryContext:
        if type(request) is not PlanRequest:
            raise TypeError("repository context requires an exact PlanRequest")
        return await self._load_epoch(_repository_epoch(request), _policy_snapshot(projection))

    async def prepare(
        self, epoch: RepositoryEpoch, policy: PolicySnapshot
    ) -> ContextPreparationOutcome:
        if self._cache.find(epoch, policy) is not None:
            return "prepared"
        acquired_at = self._cache.acquisition_time()
        if acquired_at is None:
            return "not_cached"
        context = await self._load_epoch(epoch, policy, use_prepared=False)
        if context.planning_input.full_ci_invalidating:
            return "invalid"
        return "prepared" if self._cache.put(context.planning_input, acquired_at) else "not_cached"

    def close_prepared_contexts(self) -> None:
        self._cache.close()

    async def _load_epoch(
        self, epoch: RepositoryEpoch, policy: PolicySnapshot, *, use_prepared: bool = True
    ) -> GitHubRepositoryContext:
        try:
            transport = self._transport_factory.for_installation(epoch.installation_id)
            repository = await self._resolve_repository(
                ActionsClient(transport, api_version=self._api_version),
                epoch,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            repository = None

        if repository is None:
            diff = invalid_diff(epoch, self._limits.max_diff_files)
            graph = self._fallback_graph(epoch, diff, policy)
        else:
            diff_client = DiffClient(transport, api_version=self._api_version)
            cached = self._cache.find(epoch, policy) if use_prepared else None
            if cached is not None:
                live = await current_pull_request_matches(
                    epoch,
                    repository,
                    diff_client,
                    max_response_json_bytes=self._limits.max_response_json_bytes,
                )
                if live and self._cache.current(cached):
                    self._cache_metric("cache_hit")
                    context = cached.planning_input
                    return GitHubRepositoryContext(
                        epoch, context.diff, context.dependency_graph, context
                    )
            self._cache_metric("cache_miss")
            contents_client = WorkflowCatalogClient(transport, api_version=self._api_version)
            diff = await load_diff(
                epoch,
                repository,
                diff_client,
                max_diff_files=self._limits.max_diff_files,
                max_diff_pages=self._limits.max_diff_pages,
                max_response_json_bytes=self._limits.max_response_json_bytes,
            )
            graph = await self._load_graph(
                epoch,
                diff,
                policy,
                repository,
                contents_client,
                WorkflowDiscoveryClient(transport, api_version=self._api_version),
            )
        planning_input = build_planning_input(epoch, diff, graph, policy)
        return GitHubRepositoryContext(epoch, diff, graph, planning_input)

    def _cache_metric(self, outcome: str) -> None:
        if self._observe is not None:
            self._observe(outcome)

    async def _resolve_repository(
        self,
        client: ActionsClient,
        epoch: RepositoryEpoch,
    ) -> GitHubRepository | None:
        outcome = await client.get_repository_by_id(epoch.repository_id)
        expected_path = repository_id_path(epoch.repository_id)
        if not isinstance(outcome, GitHubSuccess):
            return None
        provider_request = outcome.request
        if (
            provider_request.operation != "repositories.get_by_id"
            or provider_request.method != "GET"
            or provider_request.path != expected_path
            or provider_request.api_version != self._api_version
            or provider_request.query
            or provider_request.body is not None
            or outcome.response.pagination != GitHubPaginationEvidence.not_paginated()
        ):
            return None
        body = outcome.response.body
        if type(body) is not bytes or len(body) > self._limits.max_response_json_bytes:
            return None
        resolved = decode_repository(body)
        if resolved is None:
            return None
        repository_id, repository = resolved
        if (
            repository_id != epoch.repository_id
            or repository.owner != epoch.owner
            or repository.name != epoch.name
        ):
            return None
        return repository

    async def _load_graph(
        self,
        epoch: RepositoryEpoch,
        diff: DiffContext,
        policy: PolicySnapshot,
        repository: GitHubRepository,
        client: WorkflowCatalogClient,
        git_client: WorkflowDiscoveryClient,
    ) -> DependencyGraphContext:
        if diff.full_ci_invalidating or DEPENDENCY_GRAPH_PATH in diff.changed_paths:
            return self._fallback_graph(epoch, diff, policy)
        content = await self._load_content(
            client,
            repository,
            DEPENDENCY_GRAPH_PATH,
            epoch.head_sha,
        )
        if content is None:
            return self._fallback_graph(epoch, diff, policy)
        baseline = await self._load_content(
            client,
            repository,
            DEPENDENCY_GRAPH_PATH,
            epoch.base_sha,
        )
        if baseline != content:
            return self._fallback_graph(epoch, diff, policy)
        artifact = parse_dependency_graph_artifact(
            content,
            retrieved_for_sha=epoch.head_sha,
            trusted=True,
        )
        if artifact is None:
            return self._fallback_graph(epoch, diff, policy)
        if artifact.provenance.generator.startswith("self-ci@") and (
            artifact.provenance.generator != SELF_CI_GENERATOR
            or not await self_ci_inventory_is_current(
                git_client,
                repository,
                head_sha=epoch.head_sha,
                graph_content=content,
                artifact=artifact,
            )
        ):
            return self._fallback_graph(epoch, diff, policy)
        return build_dependency_graph(epoch, diff, policy, artifact)

    async def _load_content(
        self,
        client: WorkflowCatalogClient,
        repository: GitHubRepository,
        path: str,
        revision_sha: str,
    ) -> bytes | None:
        try:
            outcome = await client.get_content(repository, path, ref=revision_sha)
        except Exception:
            return None
        expected_request_path = f"{repository.path}/contents/{contents_path_value(path)}"
        if (
            not isinstance(outcome, GitHubSuccess)
            or outcome.request.operation != "workflow_catalog.get_content"
            or outcome.request.path != expected_request_path
            or outcome.request.query != (GitHubQueryParameter("ref", revision_sha),)
        ):
            return None
        return decode_contents_file(
            outcome.response.body,
            expected_path=path,
            max_json_bytes=self._limits.max_content_response_json_bytes,
            max_content_bytes=self._limits.max_content_bytes,
        )

    @staticmethod
    def _fallback_graph(
        epoch: RepositoryEpoch,
        diff: DiffContext,
        policy: PolicySnapshot,
    ) -> DependencyGraphContext:
        return build_dependency_graph(
            epoch,
            diff,
            policy,
            DependencyGraphArtifact(
                provenance=GraphProvenance(
                    source=policy.dependency_graph_source,
                    schema_version="",
                    generator="",
                    retrieved_for_sha=epoch.head_sha,
                    trusted=False,
                    invalidates_when_changed=(),
                ),
                nodes=(),
                global_risk_paths=policy.global_risk_paths,
            ),
        )


def _repository_epoch(request: PlanRequest) -> RepositoryEpoch:
    return RepositoryEpoch(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        owner=request.owner,
        name=request.repository,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
    )


def _policy_snapshot(projection: DynamicCiPlanningProjection | PolicySnapshot) -> PolicySnapshot:
    if type(projection) is PolicySnapshot:
        return projection
    if type(projection) is DynamicCiPlanningProjection:
        return PolicySnapshot.from_projection(projection)
    raise TypeError("repository context requires an exact planning projection or policy snapshot")
