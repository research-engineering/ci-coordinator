"""Conservative observe-only proposal generation through existing admission."""

from __future__ import annotations

from typing import Final

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.kernel import canonical_json
from ci_coordinator.workflow_discovery.proposal_model import (
    SUPPORTED_CI_EVENTS,
    ProposalEvent,
    ProposalManifest,
)
from ci_coordinator.workflow_discovery.report import DiscoveryReport
from ci_coordinator.workflow_discovery.summary import JobSummary, WorkflowSummary

_MISSING_PROOF = object()
_ALWAYS_CONDITIONS: Final = frozenset(("always()", "${{ always() }}"))
type _StableSignal = tuple[WorkflowSummary, JobSummary, tuple[ProposalEvent, ...]]
type _FactIndex = dict[tuple[str, str], object]


def generate_proposal(
    report: DiscoveryReport,
    *,
    default_branch_head: bool,
) -> ProposalManifest:
    if type(default_branch_head) is not bool:
        raise TypeError("default_branch_head must be an exact boolean")
    blockers: list[str] = []
    if not report.complete:
        blockers.append("inventory_incomplete")
    if not report.local_graph_closed:
        blockers.append("local_graph_open")
    if not default_branch_head:
        blockers.append("default_branch_head_unproven")
    if blockers:
        return ProposalManifest.blocked(report=report, blockers=tuple(blockers))

    facts = _fact_index(report)
    candidates = tuple(
        candidate for workflow in report.workflows for candidate in _stable_signals(facts, workflow)
    )
    if len(candidates) != 1:
        return ProposalManifest.blocked(
            report=report,
            blockers=(
                "stable_provider_signal_absent"
                if not candidates
                else "stable_provider_signal_ambiguous",
            ),
        )

    workflow, job, selected_events = candidates[0]
    job_name = job.provider_signal_name
    if job_name is None:
        return ProposalManifest.blocked(
            report=report,
            blockers=("stable_provider_signal_absent",),
        )
    if not _signal_identity_is_unique(
        report,
        facts,
        selected_workflow=workflow,
        selected_job=job,
        selected_name=job_name,
    ):
        return ProposalManifest.blocked(
            report=report,
            blockers=("stable_provider_signal_ambiguous",),
            selected_workflow_path=workflow.path,
            selected_job_id=job.job_id,
            selected_job_name=job_name,
            selected_events=selected_events,
        )
    source = canonical_json(
        {
            "schemaVersion": "ci-repository-policy/v1",
            "repository": {
                "installationId": report.repository.scope.installation_id,
                "repositoryId": report.repository.scope.repository_id,
                "owner": report.repository.owner,
                "name": report.repository.name,
                "defaultBranch": report.repository.default_branch,
                "rules": [
                    {
                        "name": f"discovered-{event}-default-branch-ci",
                        "on": {
                            "event": event,
                            "branches": [report.repository.default_branch],
                        },
                        "mode": "observe",
                        "expectedSignals": [
                            {
                                "kind": "workflow",
                                "name": job_name,
                                "workflowFile": workflow.path,
                                "source": "native",
                                "requiredConclusion": "success",
                                "required": True,
                            }
                        ],
                        "omittedSignals": [],
                    }
                    for event in selected_events
                ],
                "dynamicCi": None,
            },
        }
    )
    admission = admit_policy_document(source, "json")
    if not isinstance(admission, ValidatedEpochDraft):
        diagnostics = tuple(admission)
        return ProposalManifest.blocked(
            report=report,
            blockers=tuple(f"policy_admission:{item.code}" for item in diagnostics),
            selected_workflow_path=workflow.path,
            selected_job_id=job.job_id,
            selected_job_name=job_name,
            selected_events=selected_events,
            diagnostics=diagnostics,
        )
    if admission.scope != report.repository.scope or admission.source_bytes != source:
        return ProposalManifest.blocked(
            report=report,
            blockers=("policy_admission_binding_mismatch",),
            selected_workflow_path=workflow.path,
            selected_job_id=job.job_id,
            selected_job_name=job_name,
            selected_events=selected_events,
        )
    return ProposalManifest.reviewable(
        report=report,
        workflow_path=workflow.path,
        job_id=job.job_id,
        job_name=job_name,
        selected_events=selected_events,
        policy_source=source,
        admission=admission,
    )


def _stable_signals(
    facts: _FactIndex,
    workflow: WorkflowSummary,
) -> tuple[_StableSignal, ...]:
    triggers = workflow.triggers
    default_branch_events = _proven_value(
        facts,
        workflow.subject_id,
        "workflow.default_branch_ci_events",
    )
    if (
        triggers is None
        or _proven_value(facts, workflow.subject_id, "workflow.triggers") != triggers
        or "workflow_dispatch" not in triggers
        or type(default_branch_events) is not tuple
    ):
        return ()
    selected_events = tuple(
        event for event in SUPPORTED_CI_EVENTS if event in default_branch_events
    )
    if not selected_events:
        return ()

    if len(workflow.jobs) == 1:
        job = workflow.jobs[0]
        if (
            _native_signal_proven(facts, job)
            and job.condition is None
            and _proven_value(facts, job.subject_id, "job.condition.syntax") is None
            and job.needs == ()
            and _proven_value(facts, job.subject_id, "job.needs") == ()
        ):
            return ((workflow, job, selected_events),)
        return ()

    terminal_job_id = _terminal_job_id(facts, workflow)
    return tuple(
        (workflow, job, selected_events)
        for job in workflow.jobs
        if job.job_id == terminal_job_id
        and _native_signal_proven(facts, job)
        and job.condition in _ALWAYS_CONDITIONS
        and _proven_value(facts, job.subject_id, "job.condition.syntax") == job.condition
    )


def _native_signal_proven(facts: _FactIndex, job: JobSummary) -> bool:
    return (
        job.provider_signal_name is not None
        and job.matrix == ()
        and job.uses is None
        and _proven_value(facts, job.subject_id, "job.provider_signal_name")
        == job.provider_signal_name
        and _proven_value(facts, job.subject_id, "job.matrix") == ()
        and _proven_value(facts, job.subject_id, "job.uses") is None
    )


def _signal_identity_is_unique(
    report: DiscoveryReport,
    facts: _FactIndex,
    *,
    selected_workflow: WorkflowSummary,
    selected_job: JobSummary,
    selected_name: str,
) -> bool:
    selected_key = _ascii_lower(selected_name)
    for workflow in report.workflows:
        for job in workflow.jobs:
            if workflow is selected_workflow and job is selected_job:
                continue
            display_name = _effective_job_display_name(facts, job)
            if display_name is None or _ascii_lower(display_name) == selected_key:
                return False
    return True


def _effective_job_display_name(facts: _FactIndex, job: JobSummary) -> str | None:
    proven_name = _proven_value(facts, job.subject_id, "job.name")
    if proven_name != job.name:
        return None
    return job.job_id if job.name is None else job.name


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value
    )


def _terminal_job_id(
    facts: _FactIndex,
    workflow: WorkflowSummary,
) -> str | None:
    dependencies: dict[str, tuple[str, ...]] = {}
    for job in workflow.jobs:
        if job.needs is None or _proven_value(facts, job.subject_id, "job.needs") != job.needs:
            return None
        dependencies[job.job_id] = job.needs
    if any(
        dependency not in dependencies for needs in dependencies.values() for dependency in needs
    ):
        return None

    indegree = {job_id: len(needs) for job_id, needs in dependencies.items()}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in dependencies}
    for job_id, needs in dependencies.items():
        for dependency in needs:
            dependents[dependency].append(job_id)
    ready = [job_id for job_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        dependency = ready.pop()
        visited += 1
        for dependent in dependents[dependency]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited != len(dependencies):
        return None
    sinks = tuple(job_id for job_id, targets in dependents.items() if not targets)
    return sinks[0] if len(sinks) == 1 else None


def _fact_index(report: DiscoveryReport) -> _FactIndex:
    index: _FactIndex = {}
    for fact in report.facts:
        key = (fact.subject_id, fact.field)
        index[key] = fact.value if key not in index else _MISSING_PROOF
    return index


def _proven_value(facts: _FactIndex, subject_id: str, field: str) -> object:
    return facts.get((subject_id, field), _MISSING_PROOF)
