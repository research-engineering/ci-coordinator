"""Provider-independent workflow authority admission for sealed lab bytes."""

from __future__ import annotations

from ci_coordinator.execution_orchestration import TargetExecutionRegistry
from ci_coordinator.repo_context import ProviderWorkflowInventory, parse_workflow_capability


def admits_target_workflow_authority(
    registry: TargetExecutionRegistry,
    *,
    workflow_contents: tuple[tuple[str, bytes], ...],
    revision_sha: str,
) -> bool:
    """Apply the production workflow predicates to one exact laboratory epoch."""

    expected_paths = tuple(
        binding.path for binding in registry.adapter_files if binding.is_workflow
    )
    if tuple(path for path, _ in workflow_contents) != expected_paths:
        return False
    capabilities = []
    for path, content in workflow_contents:
        capability = parse_workflow_capability(
            content,
            path=path,
            revision_sha=revision_sha,
        )
        if capability is None:
            return False
        capabilities.append(capability)
    inventory = ProviderWorkflowInventory(
        revision_sha=revision_sha,
        default_branch_workflows=(),
        revision_capabilities=tuple(capabilities),
    )
    root_paths = tuple(workflow.workflow_path for workflow in registry.workflows)
    return (
        inventory.admits_local_reusable_workflow_closure(
            root_paths=root_paths,
            expected_paths=expected_paths,
        )
        and all(
            inventory.admits_static_gate(
                workflow_path=workflow.workflow_path,
                job_id=workflow.gate_job_id,
                job_name=workflow.gate_signal_name,
                required_dependencies=workflow.gate_dependencies,
                require_default_branch_activation=False,
            )
            for workflow in registry.workflows
        )
        and all(
            inventory.admits_exact_job_topology(
                workflow_path=workflow.workflow_path,
                expected=workflow.job_topology,
                require_default_branch_activation=False,
            )
            for workflow in registry.workflows
        )
        and all(
            inventory.admits_control_plane(
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
    )
