"""Independent workflow and job candidate enumeration."""

from __future__ import annotations

from ci_coordinator.execution_orchestration.target_registry import (
    TargetExecutionRegistry,
    TargetWorkflowBinding,
)
from ci_coordinator.repo_context.workflow_inventory import (
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    RevisionWorkflowCapability,
)
from ci_coordinator.target_authority_relation.model import AuthorityFieldEntry
from ci_coordinator.workflow_authority.evidence import WorkflowAuthorityEvidence
from ci_coordinator.workflow_authority.model import WorkflowManifestEntry
from ci_coordinator.workflow_discovery.report import DiscoveryReport
from ci_coordinator.workflow_discovery.summary import JobSummary, WorkflowSummary

from ._fields import authority_fields, not_applicable, present, unknown
from .model import ObservedCandidate, TargetAuthorityProducerError

_WORKFLOWS_ROOT = ".github/workflows/"


def enumerate_workflow_candidates(
    *,
    evidence: WorkflowAuthorityEvidence,
    report: DiscoveryReport,
    provider_inventory: ProviderWorkflowInventory,
    registry: TargetExecutionRegistry,
) -> tuple[ObservedCandidate, ...]:
    blobs = tuple(entry for entry in evidence.manifest.entries if entry.object_type == "blob")
    direct_workflows = tuple(entry for entry in blobs if _is_direct_workflow(entry.path))
    direct_paths = tuple(entry.path for entry in direct_workflows)
    summaries = {workflow.path: workflow for workflow in report.workflows}
    capabilities = {item.path: item for item in provider_inventory.revision_capabilities}
    defaults = {item.path: item for item in provider_inventory.default_branch_workflows}
    if set(direct_paths) != set(summaries) or set(direct_paths) != set(capabilities):
        raise TargetAuthorityProducerError(
            "workflow_domain_mismatch",
            "Git manifest, parsed workflows, and exact-revision capabilities disagree",
        )
    if set(direct_paths) != set(defaults):
        raise TargetAuthorityProducerError(
            "provider_workflow_domain_mismatch",
            "provider default-branch workflow inventory is not exact for the Git domain",
        )

    candidates: list[ObservedCandidate] = []
    for entry in blobs:
        if entry.path in summaries:
            summary = summaries[entry.path]
            capability = capabilities[entry.path]
            active = defaults[entry.path]
            _require_workflow_agreement(summary, capability)
            fields = _workflow_fields(entry, summary, capability, active, report)
        else:
            fields = _non_workflow_blob_fields(entry)
        locator = f"git:{entry.path}@{entry.object_id}"
        candidates.append(
            ObservedCandidate(f"workflow_blob:{entry.path}", "workflow_blob", locator, fields)
        )
        if entry.path not in summaries:
            continue
        summary = summaries[entry.path]
        capability = capabilities[entry.path]
        candidates.append(ObservedCandidate(f"workflow:{entry.path}", "workflow", locator, fields))
        candidates.extend(
            _job_candidates(
                entry=entry,
                summary=summary,
                capability=capability,
                registry=registry,
                report=report,
            )
        )
    return tuple(candidates)


def _workflow_fields(
    entry: WorkflowManifestEntry,
    summary: WorkflowSummary,
    capability: RevisionWorkflowCapability,
    active: DefaultBranchWorkflow,
    report: DiscoveryReport,
) -> tuple[AuthorityFieldEntry, ...]:
    content_identity = _content_identity(entry)
    trigger_field = (
        unknown("workflow trigger surface is not statically closed")
        if summary.triggers is None
        else present(list(summary.triggers))
    )
    subject_ids = {summary.subject_id, *(job.subject_id for job in summary.jobs)}
    semantic_projection = (
        unknown("workflow discovery retains unresolved safety predicates")
        if _has_safety_unknown(report, subject_ids)
        else present(
            {
                "discovery": _stable_workflow_mapping(summary),
                "providerCapability": capability.to_stable_mapping(),
            }
        )
    )
    return authority_fields(
        "workflow",
        {
            "activeState": present(
                {
                    "providerWorkflowId": active.workflow_id,
                    "state": "active" if active.active else "disabled",
                }
            ),
            "contentIdentity": present(content_identity),
            "semanticProjection": semantic_projection,
            "triggerSurface": trigger_field,
        },
    )


def _non_workflow_blob_fields(
    entry: WorkflowManifestEntry,
) -> tuple[AuthorityFieldEntry, ...]:
    return authority_fields(
        "workflow",
        {
            "activeState": present({"state": "not_provider_workflow"}),
            "contentIdentity": present(_content_identity(entry)),
            "semanticProjection": present(
                {"kind": "non_executable_workflow_tree_blob", "path": entry.path}
            ),
            "triggerSurface": present([]),
        },
    )


def _job_candidates(
    *,
    entry: WorkflowManifestEntry,
    summary: WorkflowSummary,
    capability: RevisionWorkflowCapability,
    registry: TargetExecutionRegistry,
    report: DiscoveryReport,
) -> tuple[ObservedCandidate, ...]:
    workflow_binding = registry.workflow(summary.path)
    capability_needs = dict(capability.job_needs)
    authorities = {item.job_id: item for item in capability.job_authorities}
    candidates: list[ObservedCandidate] = []
    for job in summary.jobs:
        roles = _job_roles(job.job_id, workflow_binding)
        profiles = tuple(
            profile.profile_id
            for profile in registry.profiles
            if profile.workflow_path == summary.path and profile.job_id == job.job_id
        )
        needs = capability_needs[job.job_id]
        needs_field = (
            unknown("job dependency set is not statically closed")
            if job.needs is None
            else present(list(needs))
        )
        execution_kind = (
            not_applicable()
            if workflow_binding is None or not roles
            else present(workflow_binding.execution_kind)
        )
        profile_set = not_applicable() if not profiles else present(list(profiles))
        fields = authority_fields(
            "job",
            {
                "executionKind": execution_kind,
                "needs": needs_field,
                "profileSet": profile_set,
                "roleSet": present(list(roles)),
                "semanticProjection": (
                    unknown("workflow discovery retains unresolved job safety predicates")
                    if _has_safety_unknown(report, {job.subject_id})
                    else present(
                        {
                            "discovery": _stable_job_mapping(job),
                            "providerAuthority": authorities[job.job_id].to_identity_mapping(),
                        }
                    )
                ),
                "workflowIdentity": present(_content_identity(entry)),
            },
        )
        candidates.append(
            ObservedCandidate(
                f"job:{summary.path}#{job.job_id}",
                "job",
                f"git:{summary.path}#jobs.{job.job_id}@{entry.object_id}",
                fields,
            )
        )
    return tuple(candidates)


def _require_workflow_agreement(
    summary: WorkflowSummary,
    capability: RevisionWorkflowCapability,
) -> None:
    summary_jobs = tuple(job.job_id for job in summary.jobs)
    if summary_jobs != capability.job_ids:
        raise TargetAuthorityProducerError(
            "workflow_job_domain_mismatch",
            f"workflow parsers disagree on job identities for {summary.path}",
        )
    capability_needs = dict(capability.job_needs)
    if any(
        job.needs is not None and job.needs != capability_needs[job.job_id] for job in summary.jobs
    ):
        raise TargetAuthorityProducerError(
            "workflow_needs_mismatch",
            f"workflow parsers disagree on job dependencies for {summary.path}",
        )
    if summary.triggers is not None and summary.triggers != capability.triggers:
        raise TargetAuthorityProducerError(
            "workflow_trigger_mismatch",
            f"workflow parsers disagree on triggers for {summary.path}",
        )


def _job_roles(
    job_id: str,
    workflow: TargetWorkflowBinding | None,
) -> tuple[str, ...]:
    if workflow is None:
        return ()
    roles: list[str] = []
    for role, candidate in (
        ("invocation_classifier", workflow.invocation_job_id),
        ("plan_request", workflow.plan_request_job_id),
        ("plan", workflow.plan_job_id),
        ("fallback", workflow.fallback_job_id),
        ("gate", workflow.gate_job_id),
    ):
        if candidate == job_id:
            roles.append(role)
    if job_id in workflow.execution_job_ids:
        roles.append("execution")
    if job_id in workflow.required_job_ids:
        roles.append("required_execution")
    return tuple(sorted(roles))


def _content_identity(entry: WorkflowManifestEntry) -> dict[str, object]:
    return {
        "gitObjectFormat": "sha1",
        "objectId": entry.object_id,
        "sha256": entry.blob_sha256,
        "size": entry.observed_size,
    }


def _stable_workflow_mapping(value: WorkflowSummary) -> dict[str, object]:
    return {
        "path": value.path,
        "name": value.name,
        "triggers": None if value.triggers is None else list(value.triggers),
        "permissions": (
            None if value.permissions is None else value.permissions.to_identity_mapping()
        ),
        "concurrency": (
            None if value.concurrency is None else value.concurrency.to_identity_mapping()
        ),
        "staticSecretNames": (
            None if value.static_secret_names is None else list(value.static_secret_names)
        ),
        "jobs": [_stable_job_mapping(job) for job in value.jobs],
    }


def _stable_job_mapping(value: JobSummary) -> dict[str, object]:
    mapping = value.to_identity_mapping()
    mapping.pop("subjectId")
    return mapping


def _has_safety_unknown(report: DiscoveryReport, subject_ids: set[str]) -> bool:
    return any(
        item.criticality == "safety" and item.subject_id in subject_ids for item in report.unknowns
    )


def _is_direct_workflow(path: str) -> bool:
    relative = path.removeprefix(_WORKFLOWS_ROOT)
    return "/" not in relative and relative.endswith((".yml", ".yaml"))
