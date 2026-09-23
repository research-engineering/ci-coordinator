"""Total, non-authoritative workflow adoption assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    require_canonical_text_tuple,
    require_sha256,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.adoption_target import (
    AdoptionTargetProjection,
    AdoptionTargetWorkflow,
)
from ci_coordinator.workflow_discovery.report import DiscoveryReport
from ci_coordinator.workflow_discovery.summary import WorkflowSummary

type AdoptionState = Literal[
    "in_place_job_set",
    "reusable_workflow_set",
    "witness_shards",
    "full_only",
    "invalid",
]
type RecommendedAdapter = Literal[
    "in_place_job_set",
    "reusable_workflow_set",
    "full_only",
    "none",
]
type SelectableAdapter = Literal["in_place_job_set", "reusable_workflow_set"]

ADOPTION_NON_CLAIMS: Final = tuple(
    sorted(
        (
            "assessment is not omission authority",
            "assessment is not owner approval",
            "assessment is not production admission",
            "assessment is not provider inventory proof",
            "assessment is not runtime behavior proof",
        ),
        key=utf16_sort_key,
    )
)
_OWNER_INPUTS: Final = tuple(
    sorted(
        (
            "aggregate gate contract",
            "capability-to-obligation mapping",
            "execution profile ownership",
            "full-validation fallback contract",
        ),
        key=utf16_sort_key,
    )
)


@dataclass(frozen=True, slots=True)
class AdoptionAssessment:
    workflow_path: str
    state: AdoptionState
    recommended_adapter: RecommendedAdapter
    blockers: tuple[str, ...]
    required_owner_inputs: tuple[str, ...]
    inventory_digest: str
    non_claims: tuple[str, ...]

    def __post_init__(self) -> None:
        require_workflow_path(self.workflow_path)
        if self.state not in {
            "in_place_job_set",
            "reusable_workflow_set",
            "witness_shards",
            "full_only",
            "invalid",
        }:
            raise ValueError("workflow adoption state is not admitted")
        if self.recommended_adapter not in {
            "in_place_job_set",
            "reusable_workflow_set",
            "full_only",
            "none",
        }:
            raise ValueError("workflow adoption adapter is not admitted")
        require_canonical_text_tuple(
            self.blockers,
            "workflow adoption blockers",
            allow_empty=True,
        )
        require_canonical_text_tuple(
            self.required_owner_inputs,
            "workflow adoption owner inputs",
            allow_empty=True,
        )
        require_sha256(self.inventory_digest, "workflow adoption inventory digest")
        require_canonical_text_tuple(
            self.non_claims,
            "workflow adoption non-claims",
            allow_empty=False,
        )
        if self.non_claims != ADOPTION_NON_CLAIMS:
            raise ValueError("workflow adoption non-claims must use the closed catalog")
        if self.state in {"in_place_job_set", "reusable_workflow_set"}:
            if (
                self.recommended_adapter != self.state
                or self.blockers
                or self.required_owner_inputs
            ):
                raise ValueError("selectable adoption must be closed and self-recommending")
        elif self.state == "witness_shards":
            if self.recommended_adapter != "none" or self.blockers or self.required_owner_inputs:
                raise ValueError("witness-shard adoption must be closed and non-recommending")
        elif self.state == "full_only":
            if (
                self.recommended_adapter == "none"
                or not self.blockers
                or not self.required_owner_inputs
            ):
                raise ValueError("full-only adoption must expose its missing owner proof")
        elif (
            self.recommended_adapter != "none"
            or not self.blockers
            or not self.required_owner_inputs
        ):
            raise ValueError("invalid adoption must require syntax repair")


def assess_workflow_adoption(
    report: DiscoveryReport,
    *,
    target_projection: AdoptionTargetProjection | None = None,
) -> tuple[AdoptionAssessment, ...]:
    if type(report) is not DiscoveryReport:
        raise TypeError("workflow adoption requires an exact discovery report")
    projection = (
        AdoptionTargetProjection.absent() if target_projection is None else target_projection
    )
    if type(projection) is not AdoptionTargetProjection:
        raise TypeError("workflow adoption target projection must be exact")
    target_by_path = {target.workflow_path: target for target in projection.workflows}
    workflows = {workflow.path: workflow for workflow in report.workflows}
    assessments = tuple(
        _assessment(
            report,
            workflows.get(source.path),
            workflow_path=source.path,
            projection=projection,
            target=target_by_path.get(source.path),
        )
        for source in report.sources
    )
    if tuple(item.workflow_path for item in assessments) != tuple(
        source.path for source in report.sources
    ):
        raise RuntimeError("workflow adoption assessment is not source-total")
    return assessments


def _assessment(
    report: DiscoveryReport,
    workflow: WorkflowSummary | None,
    *,
    workflow_path: str,
    projection: AdoptionTargetProjection,
    target: AdoptionTargetWorkflow | None,
) -> AdoptionAssessment:
    if workflow is None:
        return _create(
            report,
            workflow_path,
            state="invalid",
            recommended_adapter="none",
            blockers=("workflow_parse_failed",),
            required_owner_inputs=("valid workflow syntax",),
        )
    recommendation = _structural_recommendation(workflow, target=target)
    blockers: list[str] = []
    if not report.complete:
        blockers.append("inventory_incomplete")
    if not report.local_graph_closed:
        blockers.append("local_graph_open")
    if projection.status in {"invalid", "unavailable"}:
        blockers.append(f"target_semantic_projection_{projection.status}")
    elif target is None:
        blockers.append("target_semantic_projection_absent")
    else:
        blockers.extend(_target_blockers(workflow, target))
    if blockers:
        return _create(
            report,
            workflow.path,
            state="full_only",
            recommended_adapter=recommendation,
            blockers=tuple(blockers),
            required_owner_inputs=_OWNER_INPUTS,
        )
    if target is not None and target.execution_kind == "witness-shards":
        return _create(
            report,
            workflow.path,
            state="witness_shards",
            recommended_adapter="none",
            blockers=(),
            required_owner_inputs=(),
        )
    return _create(
        report,
        workflow.path,
        state=recommendation,
        recommended_adapter=recommendation,
        blockers=(),
        required_owner_inputs=(),
    )


def _target_blockers(
    workflow: WorkflowSummary,
    target: AdoptionTargetWorkflow,
) -> tuple[str, ...]:
    jobs = {job.job_id: job for job in workflow.jobs}
    blockers: list[str] = []
    expected_job_ids = {
        target.invocation_job_id,
        target.plan_request_job_id,
        target.plan_job_id,
        target.gate_job_id,
        *(job.job_id for job in target.execution_jobs),
        *(() if target.fallback_job_id is None else (target.fallback_job_id,)),
    }
    if set(jobs) - expected_job_ids:
        blockers.append("unregistered_workflow_job_present")
    invocation = jobs.get(target.invocation_job_id)
    if invocation is None:
        blockers.append("invocation_classifier_job_absent")
    elif invocation.needs != ():
        blockers.append("invocation_classifier_dependencies_mismatch")
    plan_request = jobs.get(target.plan_request_job_id)
    if plan_request is None:
        blockers.append("registered_plan_request_job_absent")
    elif plan_request.needs != (target.invocation_job_id,):
        blockers.append("registered_plan_request_dependencies_mismatch")
    plan = jobs.get(target.plan_job_id)
    if plan is None:
        blockers.append("registered_plan_job_absent")
    elif plan.needs != (target.plan_request_job_id,):
        blockers.append("registered_plan_dependencies_mismatch")
    if target.fallback_job_id is not None:
        fallback = jobs.get(target.fallback_job_id)
        if fallback is None:
            blockers.append("registered_fallback_job_absent")
        elif fallback.needs != (target.plan_job_id,):
            blockers.append("registered_fallback_dependencies_mismatch")
    if any(job.job_id not in jobs for job in target.execution_jobs):
        blockers.append("registered_execution_job_absent")
    elif any(jobs[job.job_id].needs != job.needs for job in target.execution_jobs):
        blockers.append("registered_execution_dependencies_mismatch")
    gate = jobs.get(target.gate_job_id)
    if gate is None:
        blockers.append("registered_gate_job_absent")
    else:
        if gate.provider_signal_name != target.gate_signal_name:
            blockers.append("registered_gate_signal_mismatch")
        required_gate_dependencies = tuple(
            sorted(
                expected_job_ids
                - {
                    target.invocation_job_id,
                    target.gate_job_id,
                    target.plan_request_job_id,
                },
                key=utf16_sort_key,
            )
        )
        if gate.needs != required_gate_dependencies:
            blockers.append("registered_gate_dependencies_mismatch")
        if gate.condition not in {"always()", "${{ always() }}"}:
            blockers.append("registered_gate_condition_unsafe")
    return tuple(blockers)


def _structural_recommendation(
    workflow: WorkflowSummary,
    *,
    target: AdoptionTargetWorkflow | None,
) -> SelectableAdapter:
    jobs = {job.job_id: job for job in workflow.jobs}
    execution_jobs = (
        tuple(jobs.get(job.job_id) for job in target.execution_jobs)
        if target is not None
        else workflow.jobs
    )
    return (
        "reusable_workflow_set"
        if execution_jobs
        and all(job is not None and job.uses is not None for job in execution_jobs)
        else "in_place_job_set"
    )


def _create(
    report: DiscoveryReport,
    workflow_path: str,
    *,
    state: AdoptionState,
    recommended_adapter: RecommendedAdapter,
    blockers: tuple[str, ...],
    required_owner_inputs: tuple[str, ...],
) -> AdoptionAssessment:
    return AdoptionAssessment(
        workflow_path=workflow_path,
        state=state,
        recommended_adapter=recommended_adapter,
        blockers=tuple(sorted(set(blockers), key=utf16_sort_key)),
        required_owner_inputs=required_owner_inputs,
        inventory_digest=report.inventory_digest,
        non_claims=ADOPTION_NON_CLAIMS,
    )
