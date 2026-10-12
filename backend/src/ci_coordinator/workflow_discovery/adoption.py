"""Total, non-authoritative workflow adoption assessment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_REPORT_UNKNOWNS,
    MAX_TEXT_BYTES,
    require_canonical_text_tuple,
    require_exact_tuple,
    require_identifier,
    require_sha256,
    require_text,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.adoption_target import (
    INVOCATION_CLASSIFIER_JOB_ID,
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
type SupportProfileId = Literal["github.com/github-actions-static/v5"]
type AdoptionJobRole = Literal[
    "execution",
    "fallback",
    "gate",
    "invocation_classifier",
    "plan",
    "plan_request",
]
# Control roles whose job ids the owner chooses when first registering a workflow.
type OwnerNamedControlRole = Literal["gate", "plan", "plan_request"]
type RequiredChangeAction = Literal[
    "add_control_job",
    "add_job",
    "close_report_call_graph",
    "declare_registry_workflow",
    "register_execution_job",
    "remove_or_register_job",
    "repair_other_workflow_sources",
    "repair_target_registry",
    "repair_workflow_syntax",
    "resolve_reusable_workflow_call",
    "set_job_condition",
    "set_job_name",
    "set_job_needs",
]
type _FieldShape = Literal["required", "optional", "forbidden"]

# Provisional owner decision D4 (2026-10-11): exactly one admitted support
# profile binds the GitHub.com semantics owner to one exact parser version. Any
# other parser version has no profile and therefore remains full-only.
GITHUB_COM_SUPPORT_PROFILE_ID: Final[SupportProfileId] = "github.com/github-actions-static/v5"
_ADMITTED_SUPPORT_PROFILES: Final[Mapping[str, SupportProfileId]] = MappingProxyType(
    {"github-actions-static/v5": GITHUB_COM_SUPPORT_PROFILE_ID}
)
# Blockers owned by the coordinator or provider read path; no consumer edit
# can remove them, so they carry no required consumer change.
_NON_CONSUMER_BLOCKERS: Final = frozenset(
    {"support_profile_unknown", "target_semantic_projection_unavailable"}
)
_CHANGE_ACTIONS_BY_BLOCKER: Final[Mapping[str, frozenset[RequiredChangeAction]]] = MappingProxyType(
    {
        "inventory_incomplete": frozenset({"repair_other_workflow_sources"}),
        "invocation_classifier_dependencies_mismatch": frozenset({"set_job_needs"}),
        "invocation_classifier_job_absent": frozenset({"add_job"}),
        "local_graph_open": frozenset(
            {"close_report_call_graph", "resolve_reusable_workflow_call"}
        ),
        "registered_execution_dependencies_mismatch": frozenset({"set_job_needs"}),
        "registered_execution_job_absent": frozenset({"add_job"}),
        "registered_fallback_dependencies_mismatch": frozenset({"set_job_needs"}),
        "registered_fallback_job_absent": frozenset({"add_job"}),
        "registered_gate_condition_unsafe": frozenset({"set_job_condition"}),
        "registered_gate_dependencies_mismatch": frozenset({"set_job_needs"}),
        "registered_gate_job_absent": frozenset({"add_job", "set_job_condition", "set_job_name"}),
        "registered_gate_signal_mismatch": frozenset({"set_job_name"}),
        "registered_plan_dependencies_mismatch": frozenset({"set_job_needs"}),
        "registered_plan_job_absent": frozenset({"add_job"}),
        "registered_plan_request_dependencies_mismatch": frozenset({"set_job_needs"}),
        "registered_plan_request_job_absent": frozenset({"add_job"}),
        "target_semantic_projection_absent": frozenset(
            {
                "add_control_job",
                "add_job",
                "declare_registry_workflow",
                "register_execution_job",
                "remove_or_register_job",
                "set_job_condition",
                "set_job_needs",
            }
        ),
        "target_semantic_projection_invalid": frozenset({"repair_target_registry"}),
        "unregistered_workflow_job_present": frozenset({"remove_or_register_job"}),
        "workflow_parse_failed": frozenset({"repair_workflow_syntax"}),
    }
)
# Per action: (role present, job id shape, needs shape, expected value present).
_CHANGE_SHAPES: Final[
    Mapping[RequiredChangeAction, tuple[bool, _FieldShape, _FieldShape, bool]]
] = MappingProxyType(
    {
        "add_control_job": (True, "forbidden", "required", False),
        "add_job": (True, "required", "required", False),
        "close_report_call_graph": (False, "forbidden", "forbidden", False),
        "declare_registry_workflow": (False, "forbidden", "forbidden", False),
        "register_execution_job": (True, "required", "optional", False),
        "remove_or_register_job": (False, "required", "forbidden", False),
        "repair_other_workflow_sources": (False, "forbidden", "forbidden", False),
        "repair_target_registry": (False, "forbidden", "forbidden", False),
        "repair_workflow_syntax": (False, "forbidden", "forbidden", False),
        "resolve_reusable_workflow_call": (False, "required", "forbidden", False),
        "set_job_condition": (True, "optional", "forbidden", True),
        "set_job_name": (True, "required", "forbidden", True),
        "set_job_needs": (True, "required", "required", False),
    }
)
_ADOPTION_JOB_ROLES: Final = frozenset(
    {"execution", "fallback", "gate", "invocation_classifier", "plan", "plan_request"}
)
_OWNER_NAMED_CONTROL_ROLES: Final[tuple[OwnerNamedControlRole, ...]] = (
    "gate",
    "plan",
    "plan_request",
)
_SAFE_GATE_CONDITION: Final = "always()"
# Job-derived changes are bounded by twice the report job bound; registry,
# control-role and report-level changes stay far below the remainder.
MAX_ADOPTION_REQUIRED_CHANGES: Final = 4_096

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
class AdoptionRequiredChange:
    """One closed-vocabulary consumer edit that removes one assessment blocker.

    The complete required ``needs`` of the named job are ``needs`` plus the
    owner-chosen job ids of ``needs_control_roles``. ``needs`` is null only for
    an execution registration whose current dependencies are an explicit
    unknown and must be declared statically.
    """

    blocker: str
    action: RequiredChangeAction
    role: AdoptionJobRole | None
    job_id: str | None
    needs: tuple[str, ...] | None
    needs_control_roles: tuple[OwnerNamedControlRole, ...]
    expected_value: str | None

    def __post_init__(self) -> None:
        actions = _CHANGE_ACTIONS_BY_BLOCKER.get(self.blocker)
        shape = _CHANGE_SHAPES.get(self.action)
        if actions is None or shape is None or self.action not in actions:
            raise ValueError("adoption required change is not admitted for its blocker")
        has_role, job_id_shape, needs_shape, has_expected_value = shape
        if (self.role is not None) != has_role or (
            self.role is not None and self.role not in _ADOPTION_JOB_ROLES
        ):
            raise ValueError("adoption required change role is not admitted")
        if self.action == "register_execution_job" and self.role != "execution":
            raise ValueError("execution registration must use the execution role")
        if self.action == "add_control_job" and self.role not in _OWNER_NAMED_CONTROL_ROLES:
            raise ValueError("only owner-named control roles can be added without a job id")
        if (job_id_shape == "required" and self.job_id is None) or (
            job_id_shape == "forbidden" and self.job_id is not None
        ):
            raise ValueError("adoption required change job identity is not admitted")
        if self.job_id is not None:
            require_text(self.job_id, "adoption required change job id", maximum_bytes=128)
        if self.role == "invocation_classifier" and self.job_id != INVOCATION_CLASSIFIER_JOB_ID:
            raise ValueError("the invocation classifier job id is fixed, not owner-selected")
        if (needs_shape == "required" and self.needs is None) or (
            needs_shape == "forbidden" and self.needs is not None
        ):
            raise ValueError("adoption required change dependencies are not admitted")
        if self.needs is not None:
            require_canonical_text_tuple(
                self.needs,
                "adoption required change dependencies",
                allow_empty=True,
            )
        if (
            type(self.needs_control_roles) is not tuple
            or any(role not in _OWNER_NAMED_CONTROL_ROLES for role in self.needs_control_roles)
            or self.needs_control_roles
            != tuple(sorted(set(self.needs_control_roles), key=utf16_sort_key))
            or self.role in self.needs_control_roles
        ):
            raise ValueError("adoption required change control dependencies are not admitted")
        if (
            (self.role is not None and self.job_id is None) or self.needs_control_roles
        ) and self.blocker != "target_semantic_projection_absent":
            raise ValueError("owner-named control jobs exist only before workflow registration")
        if self.action == "register_execution_job" and self.needs_control_roles != ("plan",):
            raise ValueError("execution registration must depend directly on the plan job")
        if (self.expected_value is not None) != has_expected_value:
            raise ValueError("adoption required change expected value is not admitted")
        if self.expected_value is not None:
            require_text(
                self.expected_value,
                "adoption required change expected value",
                maximum_bytes=MAX_TEXT_BYTES,
            )

    @property
    def ordering_key(self) -> tuple[bytes, bytes, bytes, bytes]:
        return (
            utf16_sort_key(self.blocker),
            utf16_sort_key(self.action),
            utf16_sort_key(self.role or ""),
            utf16_sort_key(self.job_id or ""),
        )


@dataclass(frozen=True, slots=True)
class AdoptionAssessment:
    workflow_path: str
    state: AdoptionState
    recommended_adapter: RecommendedAdapter
    support_profile_id: SupportProfileId | None
    blockers: tuple[str, ...]
    required_owner_inputs: tuple[str, ...]
    required_changes: tuple[AdoptionRequiredChange, ...]
    unknown_ids: tuple[str, ...]
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
        if any(
            blocker not in _CHANGE_ACTIONS_BY_BLOCKER and blocker not in _NON_CONSUMER_BLOCKERS
            for blocker in self.blockers
        ):
            raise ValueError("workflow adoption blocker is not in the closed vocabulary")
        if self.support_profile_id is None:
            if "support_profile_unknown" not in self.blockers:
                raise ValueError("unknown support profile must be an explicit blocker")
        elif (
            self.support_profile_id not in _ADMITTED_SUPPORT_PROFILES.values()
            or "support_profile_unknown" in self.blockers
        ):
            raise ValueError("workflow adoption support profile is not admitted")
        _require_required_changes(self.required_changes, self.blockers)
        require_canonical_text_tuple(
            self.unknown_ids,
            "workflow adoption unknown ids",
            allow_empty=True,
        )
        if len(self.unknown_ids) > MAX_REPORT_UNKNOWNS:
            raise ValueError("workflow adoption unknown ids exceed the report bound")
        for unknown_id in self.unknown_ids:
            require_identifier(unknown_id, "unknown:", "workflow adoption unknown id")
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


def support_profile_for(parser_version: str) -> SupportProfileId | None:
    """Return the admitted support profile for one exact parser version."""
    return _ADMITTED_SUPPORT_PROFILES.get(parser_version)


def _require_required_changes(
    changes: tuple[AdoptionRequiredChange, ...],
    blockers: tuple[str, ...],
) -> None:
    require_exact_tuple(changes, AdoptionRequiredChange, "workflow adoption required changes")
    if len(changes) > MAX_ADOPTION_REQUIRED_CHANGES:
        raise ValueError("workflow adoption required changes exceed their bound")
    keys = tuple(change.ordering_key for change in changes)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("workflow adoption required changes must be sorted and unique")
    if {change.blocker for change in changes} != set(blockers) - _NON_CONSUMER_BLOCKERS:
        raise ValueError("every consumer-actionable blocker needs exactly its own changes")


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
    unknown_ids_by_path: dict[str, list[str]] = {}
    for unknown in report.unknowns:
        unknown_ids_by_path.setdefault(unknown.provenance.workflow_path, []).append(
            unknown.unknown_id
        )
    support_profile_id = support_profile_for(report.parser_version)
    assessments = tuple(
        _assessment(
            report,
            workflows.get(source.path),
            workflow_path=source.path,
            projection=projection,
            target=target_by_path.get(source.path),
            support_profile_id=support_profile_id,
            unknown_ids=tuple(unknown_ids_by_path.get(source.path, ())),
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
    support_profile_id: SupportProfileId | None,
    unknown_ids: tuple[str, ...],
) -> AdoptionAssessment:
    profile_blockers = () if support_profile_id is not None else ("support_profile_unknown",)
    if workflow is None:
        return _create(
            report,
            workflow_path,
            state="invalid",
            recommended_adapter="none",
            support_profile_id=support_profile_id,
            blockers=(*profile_blockers, "workflow_parse_failed"),
            required_owner_inputs=("valid workflow syntax",),
            required_changes=(_change("workflow_parse_failed", "repair_workflow_syntax"),),
            unknown_ids=unknown_ids,
        )
    recommendation = _structural_recommendation(workflow, target=target)
    blockers: list[str] = list(profile_blockers)
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
            support_profile_id=support_profile_id,
            blockers=tuple(blockers),
            required_owner_inputs=_OWNER_INPUTS,
            required_changes=_required_changes(
                report,
                workflow,
                blockers=frozenset(blockers),
                target=target,
            ),
            unknown_ids=unknown_ids,
        )
    if target is not None and target.execution_kind == "witness-shards":
        return _create(
            report,
            workflow.path,
            state="witness_shards",
            recommended_adapter="none",
            support_profile_id=support_profile_id,
            blockers=(),
            required_owner_inputs=(),
            required_changes=(),
            unknown_ids=unknown_ids,
        )
    return _create(
        report,
        workflow.path,
        state=recommendation,
        recommended_adapter=recommendation,
        support_profile_id=support_profile_id,
        blockers=(),
        required_owner_inputs=(),
        required_changes=(),
        unknown_ids=unknown_ids,
    )


def _target_blockers(
    workflow: WorkflowSummary,
    target: AdoptionTargetWorkflow,
) -> tuple[str, ...]:
    jobs = {job.job_id: job for job in workflow.jobs}
    blockers: list[str] = []
    expected_job_ids = _expected_job_ids(target)
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
    if any(
        job.job_id in jobs and jobs[job.job_id].needs != job.needs for job in target.execution_jobs
    ):
        blockers.append("registered_execution_dependencies_mismatch")
    gate = jobs.get(target.gate_job_id)
    if gate is None:
        blockers.append("registered_gate_job_absent")
    else:
        if gate.provider_signal_name != target.gate_signal_name:
            blockers.append("registered_gate_signal_mismatch")
        if gate.needs != _required_gate_dependencies(target):
            blockers.append("registered_gate_dependencies_mismatch")
        if gate.condition not in {_SAFE_GATE_CONDITION, "${{ always() }}"}:
            blockers.append("registered_gate_condition_unsafe")
    return tuple(blockers)


def _expected_job_ids(target: AdoptionTargetWorkflow) -> set[str]:
    return {
        target.invocation_job_id,
        target.plan_request_job_id,
        target.plan_job_id,
        target.gate_job_id,
        *target.execution_job_ids,
        *(() if target.fallback_job_id is None else (target.fallback_job_id,)),
    }


def _required_gate_dependencies(target: AdoptionTargetWorkflow) -> tuple[str, ...]:
    return tuple(
        sorted(
            _expected_job_ids(target)
            - {target.invocation_job_id, target.gate_job_id, target.plan_request_job_id},
            key=utf16_sort_key,
        )
    )


def _required_changes(
    report: DiscoveryReport,
    workflow: WorkflowSummary,
    *,
    blockers: frozenset[str],
    target: AdoptionTargetWorkflow | None,
) -> tuple[AdoptionRequiredChange, ...]:
    """Derive the exact consumer edits named by each consumer-actionable blocker."""
    changes: list[AdoptionRequiredChange] = []
    if "inventory_incomplete" in blockers:
        changes.append(_change("inventory_incomplete", "repair_other_workflow_sources"))
    if "local_graph_open" in blockers:
        open_call_job_ids = sorted(
            {
                edge.caller_job_id
                for edge in report.call_edges
                if edge.caller_workflow_path == workflow.path
                and edge.kind != "remote"
                and not (edge.kind == "local" and edge.status == "resolved")
            },
            key=utf16_sort_key,
        )
        changes.extend(
            _change("local_graph_open", "resolve_reusable_workflow_call", job_id=job_id)
            for job_id in open_call_job_ids
        )
        if not open_call_job_ids:
            changes.append(_change("local_graph_open", "close_report_call_graph"))
    if "target_semantic_projection_invalid" in blockers:
        changes.append(_change("target_semantic_projection_invalid", "repair_target_registry"))
    if "target_semantic_projection_absent" in blockers:
        changes.extend(_unregistered_workflow_changes(workflow))
    if target is not None:
        changes.extend(_target_changes(workflow, target, blockers=blockers))
    return tuple(sorted(changes, key=lambda change: change.ordering_key))


def _unregistered_workflow_changes(
    workflow: WorkflowSummary,
) -> tuple[AdoptionRequiredChange, ...]:
    """Name one registry-admissible topology for a workflow without a registration.

    The invocation classifier id is fixed by the target registry; plan request,
    plan and gate ids are owner-chosen and referenced by role. Every other
    current job becomes an execution job that keeps its current dependencies on
    other execution jobs and gains the direct plan dependency; unknown current
    dependencies stay unknown rather than being guessed.
    """
    blocker = "target_semantic_projection_absent"
    classifier = INVOCATION_CLASSIFIER_JOB_ID
    classifier_key = classifier.lower()
    execution_jobs = tuple(job for job in workflow.jobs if job.job_id.lower() != classifier_key)
    execution_job_ids = frozenset(job.job_id for job in execution_jobs)
    current_classifier = next((job for job in workflow.jobs if job.job_id == classifier), None)
    changes: list[AdoptionRequiredChange] = [
        _change(blocker, "declare_registry_workflow"),
        _change(blocker, "add_control_job", role="plan_request", needs=(classifier,)),
        _change(
            blocker, "add_control_job", role="plan", needs=(), needs_control_roles=("plan_request",)
        ),
        _change(
            blocker,
            "add_control_job",
            role="gate",
            needs=tuple(sorted(execution_job_ids, key=utf16_sort_key)),
            needs_control_roles=("plan",),
        ),
        _change(blocker, "set_job_condition", role="gate", expected_value=_SAFE_GATE_CONDITION),
    ]
    if current_classifier is None:
        changes.append(
            _change(blocker, "add_job", role="invocation_classifier", job_id=classifier, needs=())
        )
    elif current_classifier.needs != ():
        changes.append(
            _change(
                blocker,
                "set_job_needs",
                role="invocation_classifier",
                job_id=classifier,
                needs=(),
            )
        )
    # A case variant of the fixed classifier id aliases it in GitHub
    # expressions, so it cannot hold any registered role.
    changes.extend(
        _change(blocker, "remove_or_register_job", job_id=job.job_id)
        for job in workflow.jobs
        if job.job_id.lower() == classifier_key and job.job_id != classifier
    )
    changes.extend(
        _change(
            blocker,
            "register_execution_job",
            role="execution",
            job_id=job.job_id,
            needs=(
                None
                if job.needs is None
                else tuple(
                    dependency for dependency in job.needs if dependency in execution_job_ids
                )
            ),
            needs_control_roles=("plan",),
        )
        for job in execution_jobs
    )
    return tuple(changes)


def _target_changes(
    workflow: WorkflowSummary,
    target: AdoptionTargetWorkflow,
    *,
    blockers: frozenset[str],
) -> tuple[AdoptionRequiredChange, ...]:
    jobs = {job.job_id: job for job in workflow.jobs}
    changes: list[AdoptionRequiredChange] = [
        _change("unregistered_workflow_job_present", "remove_or_register_job", job_id=job_id)
        for job_id in sorted(set(jobs) - _expected_job_ids(target), key=utf16_sort_key)
    ]
    roles: tuple[tuple[str, AdoptionJobRole, str | None, tuple[str, ...]], ...] = (
        ("invocation_classifier", "invocation_classifier", target.invocation_job_id, ()),
        (
            "registered_plan_request",
            "plan_request",
            target.plan_request_job_id,
            (target.invocation_job_id,),
        ),
        ("registered_plan", "plan", target.plan_job_id, (target.plan_request_job_id,)),
        ("registered_fallback", "fallback", target.fallback_job_id, (target.plan_job_id,)),
        ("registered_gate", "gate", target.gate_job_id, _required_gate_dependencies(target)),
    )
    for prefix, role, job_id, needs in roles:
        if job_id is None:
            continue
        if f"{prefix}_job_absent" in blockers:
            changes.append(
                _change(f"{prefix}_job_absent", "add_job", role=role, job_id=job_id, needs=needs)
            )
        if f"{prefix}_dependencies_mismatch" in blockers:
            changes.append(
                _change(
                    f"{prefix}_dependencies_mismatch",
                    "set_job_needs",
                    role=role,
                    job_id=job_id,
                    needs=needs,
                )
            )
    gate_expectations: tuple[tuple[str, RequiredChangeAction, str], ...] = (
        ("registered_gate_job_absent", "set_job_name", target.gate_signal_name),
        ("registered_gate_job_absent", "set_job_condition", _SAFE_GATE_CONDITION),
        ("registered_gate_signal_mismatch", "set_job_name", target.gate_signal_name),
        ("registered_gate_condition_unsafe", "set_job_condition", _SAFE_GATE_CONDITION),
    )
    changes.extend(
        _change(
            blocker,
            action,
            role="gate",
            job_id=target.gate_job_id,
            expected_value=expected_value,
        )
        for blocker, action, expected_value in gate_expectations
        if blocker in blockers
    )
    for execution_job in target.execution_jobs:
        current = jobs.get(execution_job.job_id)
        if current is None and "registered_execution_job_absent" in blockers:
            changes.append(
                _change(
                    "registered_execution_job_absent",
                    "add_job",
                    role="execution",
                    job_id=execution_job.job_id,
                    needs=execution_job.needs,
                )
            )
        elif (
            current is not None
            and current.needs != execution_job.needs
            and "registered_execution_dependencies_mismatch" in blockers
        ):
            changes.append(
                _change(
                    "registered_execution_dependencies_mismatch",
                    "set_job_needs",
                    role="execution",
                    job_id=execution_job.job_id,
                    needs=execution_job.needs,
                )
            )
    return tuple(changes)


def _change(
    blocker: str,
    action: RequiredChangeAction,
    *,
    role: AdoptionJobRole | None = None,
    job_id: str | None = None,
    needs: tuple[str, ...] | None = None,
    needs_control_roles: tuple[OwnerNamedControlRole, ...] = (),
    expected_value: str | None = None,
) -> AdoptionRequiredChange:
    return AdoptionRequiredChange(
        blocker=blocker,
        action=action,
        role=role,
        job_id=job_id,
        needs=needs,
        needs_control_roles=needs_control_roles,
        expected_value=expected_value,
    )


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
    support_profile_id: SupportProfileId | None,
    blockers: tuple[str, ...],
    required_owner_inputs: tuple[str, ...],
    required_changes: tuple[AdoptionRequiredChange, ...],
    unknown_ids: tuple[str, ...],
) -> AdoptionAssessment:
    return AdoptionAssessment(
        workflow_path=workflow_path,
        state=state,
        recommended_adapter=recommended_adapter,
        support_profile_id=support_profile_id,
        blockers=tuple(sorted(set(blockers), key=utf16_sort_key)),
        required_owner_inputs=required_owner_inputs,
        required_changes=required_changes,
        unknown_ids=unknown_ids,
        inventory_digest=report.inventory_digest,
        non_claims=ADOPTION_NON_CLAIMS,
    )
