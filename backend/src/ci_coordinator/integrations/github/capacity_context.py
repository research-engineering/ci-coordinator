"""Revision-bound GitHub inputs for runner-capacity optimization."""

from __future__ import annotations

import asyncio
from typing import Final

from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.adapter_snapshot import GitHubAdapterSnapshotLoader
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.git_snapshot import GitHubGitSnapshotReader
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.integrations.github.workflow_discovery_client import (
    WorkflowDiscoveryClient,
)
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    is_github_object_id,
)
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.runner_capacity import (
    MAX_TEST_MANIFEST_BYTES,
    TrustedCapacityManifest,
    TrustedExecutionInputs,
    bind_capacity_class_selectors,
    parse_test_manifest,
)

CAPACITY_MANIFEST_PATH: Final = ".ci-coordinator/test-manifest.v1.json"


class GitHubCapacityInputsProvider:
    """Load exact-revision target authority and optional shard-capacity evidence."""

    def __init__(self, transport_factory: InstallationTransportFactory) -> None:
        self._transport_factory = transport_factory

    async def load(
        self,
        request: PlanRequest,
        execution_authority_sha: str,
        /,
    ) -> TrustedExecutionInputs | None:
        if type(request) is not PlanRequest:
            raise TypeError("capacity context requires exact request and authority revision")
        if not is_github_object_id(execution_authority_sha):
            return None
        try:
            transport = self._transport_factory.for_installation(request.installation_id)
            git_client = WorkflowDiscoveryClient(
                transport,
                api_version=GITHUB_API_VERSION,
            )
            repository = GitHubRepository(request.owner, request.repository)
            snapshot = await GitHubAdapterSnapshotLoader(git_client).load(
                repository,
                revision_sha=execution_authority_sha,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if snapshot is None:
            return None
        registry = snapshot.registry
        inventory = snapshot.workflow_inventory
        workflow_adapter_files = tuple(item for item in registry.adapter_files if item.is_workflow)
        required_paths = tuple(item.path for item in workflow_adapter_files)
        if (
            not inventory.admits_local_reusable_workflow_closure(
                root_paths=tuple(
                    sorted(
                        (item.workflow_path for item in registry.workflows),
                        key=utf16_sort_key,
                    )
                ),
                expected_paths=required_paths,
            )
            or any(
                not inventory.admits_static_gate(
                    workflow_path=item.workflow_path,
                    job_id=item.gate_job_id,
                    job_name=item.gate_signal_name,
                    required_dependencies=item.gate_dependencies,
                    require_default_branch_activation=False,
                )
                for item in registry.workflows
            )
            or any(
                not inventory.admits_exact_job_topology(
                    workflow_path=workflow.workflow_path,
                    expected=workflow.job_topology,
                    require_default_branch_activation=False,
                )
                for workflow in registry.workflows
            )
            or any(
                not inventory.admits_control_plane(
                    workflow_path=workflow.workflow_path,
                    execution_kind=workflow.execution_kind,
                    invocation_job_id=workflow.invocation_job_id,
                    plan_request_job_id=workflow.plan_request_job_id,
                    plan_request_workflow_ref=workflow.plan_request_workflow_ref,
                    plan_job_id=workflow.plan_job_id,
                    fallback_job_id=workflow.fallback_job_id,
                    gate_job_id=workflow.gate_job_id,
                    execution_job_ids=workflow.execution_job_ids,
                )
                for workflow in registry.workflows
            )
        ):
            return None
        selectors = bind_capacity_class_selectors(registry, inventory)
        target_only = TrustedExecutionInputs(
            target_registry=registry,
            capacity_manifest=None,
            capacity_selectors=selectors,
        )
        if all(workflow.execution_kind == "native-job-set" for workflow in registry.workflows):
            return target_only
        try:
            manifest_content = await GitHubGitSnapshotReader(git_client).regular_file(
                repository,
                revision_sha=request.head_sha,
                path=CAPACITY_MANIFEST_PATH,
                maximum_bytes=MAX_TEST_MANIFEST_BYTES,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return target_only
        if manifest_content is None:
            return target_only
        manifest = parse_test_manifest(manifest_content)
        if manifest is None:
            return target_only
        return TrustedExecutionInputs(
            target_registry=registry,
            capacity_manifest=TrustedCapacityManifest(
                tests=manifest.tests,
                duration_history=manifest.duration_history,
            ),
            capacity_selectors=selectors,
        )
