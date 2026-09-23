"""Parsed workflow summaries and closed predicate catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_TEXT_BYTES,
    require_canonical_text_tuple,
    require_exact_tuple,
    require_text,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.evidence import (
    Fact,
    FactScalar,
    Unknown,
    require_canonical_evidence,
)

WORKFLOW_EXPECTED_PREDICATES: Final = (
    "workflow.check_identity",
    "workflow.concurrency",
    "workflow.default_branch_ci_events",
    "workflow.fork_exposure",
    "workflow.job_ids",
    "workflow.name",
    "workflow.oidc_availability",
    "workflow.permissions.declared",
    "workflow.permissions.effective",
    "workflow.secrets.static_names",
    "workflow.triggers",
)
JOB_EXPECTED_PREDICATES: Final = (
    "job.concurrency",
    "job.condition.effective",
    "job.condition.syntax",
    "job.environment.declared",
    "job.environment.protection",
    "job.matrix",
    "job.name",
    "job.needs",
    "job.oidc_availability",
    "job.permissions.declared",
    "job.permissions.effective",
    "job.provider_signal_name",
    "job.runner_availability",
    "job.runs_on.declared",
    "job.secrets.existence",
    "job.secrets.static_names",
    "job.service_ids",
    "job.step_run_presence",
    "job.step_uses",
    "job.timeout_minutes",
    "job.uses",
)


@dataclass(frozen=True, slots=True)
class PermissionEntry:
    name: str
    level: str

    def __post_init__(self) -> None:
        require_text(self.name, "permission name", maximum_bytes=128)
        require_text(self.level, "permission level", maximum_bytes=128)

    def to_identity_mapping(self) -> dict[str, str]:
        return {"name": self.name, "level": self.level}


@dataclass(frozen=True, slots=True)
class DeclaredPermissions:
    kind: Literal["absent", "all", "entries"]
    all_level: str | None = None
    entries: tuple[PermissionEntry, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"absent", "all", "entries"}:
            raise ValueError("declared permissions kind is not admitted")
        require_exact_tuple(self.entries, PermissionEntry, "permission entries")
        names = tuple(entry.name for entry in self.entries)
        if names != tuple(sorted(set(names), key=utf16_sort_key)):
            raise ValueError("permission entries must have unique canonical names")
        if self.kind == "all":
            if self.all_level not in {"read-all", "write-all"} or self.entries:
                raise ValueError("all permissions require one admitted aggregate level")
        elif self.all_level is not None:
            raise ValueError("aggregate permission level is reserved for all permissions")
        if self.kind == "absent" and self.entries:
            raise ValueError("absent permissions cannot contain entries")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "allLevel": self.all_level,
            "entries": [entry.to_identity_mapping() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class ConcurrencySummary:
    group: str | None
    cancel_in_progress: bool | None

    def __post_init__(self) -> None:
        if self.group is not None:
            require_text(self.group, "concurrency group", maximum_bytes=MAX_TEXT_BYTES)
        if self.cancel_in_progress is not None and type(self.cancel_in_progress) is not bool:
            raise TypeError("cancel_in_progress must be an exact boolean or absent")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"group": self.group, "cancelInProgress": self.cancel_in_progress}


@dataclass(frozen=True, slots=True)
class MatrixDimension:
    name: str
    values: tuple[FactScalar, ...]

    def __post_init__(self) -> None:
        require_text(self.name, "matrix dimension", maximum_bytes=128)
        if type(self.values) is not tuple or not self.values or len(self.values) > 256:
            raise ValueError("matrix values must be a bounded non-empty tuple")
        if any(type(value) not in {type(None), bool, int, float, str} for value in self.values):
            raise TypeError("matrix values must be exact scalar values")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"name": self.name, "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class StepSummary:
    index: int
    name: str | None
    uses: str | None
    uses_dynamic: bool
    has_run: bool
    condition: str | None

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("step index must be a non-negative integer")
        for field_name, value in (("step name", self.name), ("step uses", self.uses)):
            if value is not None:
                require_text(value, field_name, maximum_bytes=MAX_TEXT_BYTES)
        if self.condition is not None:
            require_text(
                self.condition,
                "step condition",
                maximum_bytes=MAX_TEXT_BYTES,
                allow_empty=True,
            )
        if type(self.uses_dynamic) is not bool or type(self.has_run) is not bool:
            raise TypeError("step flags must be exact booleans")
        if self.uses_dynamic and self.uses is None:
            raise ValueError("dynamic step uses must retain its declared syntax")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "index": self.index,
            "name": self.name,
            "uses": self.uses,
            "usesDynamic": self.uses_dynamic,
            "hasRun": self.has_run,
            "condition": self.condition,
        }


@dataclass(frozen=True, slots=True)
class JobSummary:
    subject_id: str
    job_id: str
    name: str | None
    needs: tuple[str, ...] | None
    uses: str | None
    uses_dynamic: bool
    runs_on: tuple[str, ...] | None
    permissions: DeclaredPermissions | None
    environment: str | None
    service_ids: tuple[str, ...] | None
    timeout_minutes: int | None
    concurrency: ConcurrencySummary | None
    matrix: tuple[MatrixDimension, ...] | None
    condition: str | None
    steps: tuple[StepSummary, ...] | None
    static_secret_names: tuple[str, ...] | None
    provider_signal_name: str | None

    def __post_init__(self) -> None:
        require_text(self.subject_id, "job subject id", maximum_bytes=128)
        require_text(self.job_id, "job id", maximum_bytes=128)
        if self.name is not None:
            require_text(self.name, "job name", maximum_bytes=MAX_TEXT_BYTES)
        for values, name in (
            (self.needs, "job needs"),
            (self.runs_on, "runner labels"),
            (self.service_ids, "service ids"),
            (self.static_secret_names, "static secret names"),
        ):
            if values is not None:
                require_canonical_text_tuple(values, name, allow_empty=True)
        if self.uses is not None:
            require_text(self.uses, "job uses", maximum_bytes=MAX_TEXT_BYTES)
        if type(self.uses_dynamic) is not bool:
            raise TypeError("job uses_dynamic must be an exact boolean")
        if self.uses_dynamic and self.uses is None:
            raise ValueError("dynamic job uses must retain its declared syntax")
        if self.permissions is not None and type(self.permissions) is not DeclaredPermissions:
            raise TypeError("job permissions must be exact declared permissions or unknown")
        if self.environment is not None:
            require_text(self.environment, "job environment", maximum_bytes=MAX_TEXT_BYTES)
        if self.timeout_minutes is not None and (
            type(self.timeout_minutes) is not int or not 1 <= self.timeout_minutes <= 2_147_483_647
        ):
            raise ValueError("job timeout must be a bounded positive integer")
        if self.concurrency is not None and type(self.concurrency) is not ConcurrencySummary:
            raise TypeError("job concurrency must be exact or unknown")
        if self.matrix is not None:
            require_exact_tuple(self.matrix, MatrixDimension, "matrix dimensions")
            names = tuple(item.name for item in self.matrix)
            if names != tuple(sorted(set(names), key=utf16_sort_key)):
                raise ValueError("matrix dimensions must have unique canonical names")
        if self.condition is not None:
            require_text(
                self.condition,
                "job condition",
                maximum_bytes=MAX_TEXT_BYTES,
                allow_empty=True,
            )
        if self.steps is not None:
            require_exact_tuple(self.steps, StepSummary, "job steps")
            if tuple(step.index for step in self.steps) != tuple(range(len(self.steps))):
                raise ValueError("job step indexes must be contiguous")
        if self.provider_signal_name is not None:
            require_text(
                self.provider_signal_name,
                "provider signal name",
                maximum_bytes=MAX_TEXT_BYTES,
            )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "subjectId": self.subject_id,
            "jobId": self.job_id,
            "name": self.name,
            "needs": None if self.needs is None else list(self.needs),
            "uses": self.uses,
            "usesDynamic": self.uses_dynamic,
            "runsOn": None if self.runs_on is None else list(self.runs_on),
            "permissions": (
                None if self.permissions is None else self.permissions.to_identity_mapping()
            ),
            "environment": self.environment,
            "serviceIds": None if self.service_ids is None else list(self.service_ids),
            "timeoutMinutes": self.timeout_minutes,
            "concurrency": (
                None if self.concurrency is None else self.concurrency.to_identity_mapping()
            ),
            "matrix": (
                None
                if self.matrix is None
                else [dimension.to_identity_mapping() for dimension in self.matrix]
            ),
            "condition": self.condition,
            "steps": (
                None if self.steps is None else [step.to_identity_mapping() for step in self.steps]
            ),
            "staticSecretNames": (
                None if self.static_secret_names is None else list(self.static_secret_names)
            ),
            "providerSignalName": self.provider_signal_name,
        }


@dataclass(frozen=True, slots=True)
class WorkflowSummary:
    subject_id: str
    path: str
    name: str | None
    triggers: tuple[str, ...] | None
    permissions: DeclaredPermissions | None
    concurrency: ConcurrencySummary | None
    static_secret_names: tuple[str, ...] | None
    jobs: tuple[JobSummary, ...]

    def __post_init__(self) -> None:
        require_text(self.subject_id, "workflow subject id", maximum_bytes=128)
        require_workflow_path(self.path)
        if self.name is not None:
            require_text(self.name, "workflow name", maximum_bytes=MAX_TEXT_BYTES)
        if self.triggers is not None:
            require_canonical_text_tuple(self.triggers, "workflow triggers", allow_empty=True)
        if self.permissions is not None and type(self.permissions) is not DeclaredPermissions:
            raise TypeError("workflow permissions must be exact declared permissions or unknown")
        if self.concurrency is not None and type(self.concurrency) is not ConcurrencySummary:
            raise TypeError("workflow concurrency must be exact or unknown")
        if self.static_secret_names is not None:
            require_canonical_text_tuple(
                self.static_secret_names,
                "workflow static secret names",
                allow_empty=True,
            )
        require_exact_tuple(self.jobs, JobSummary, "workflow jobs")
        job_ids = tuple(job.job_id for job in self.jobs)
        if job_ids != tuple(sorted(set(job_ids), key=utf16_sort_key)):
            raise ValueError("workflow jobs must have unique canonical ids")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "subjectId": self.subject_id,
            "path": self.path,
            "name": self.name,
            "triggers": None if self.triggers is None else list(self.triggers),
            "permissions": (
                None if self.permissions is None else self.permissions.to_identity_mapping()
            ),
            "concurrency": (
                None if self.concurrency is None else self.concurrency.to_identity_mapping()
            ),
            "staticSecretNames": (
                None if self.static_secret_names is None else list(self.static_secret_names)
            ),
            "jobs": [job.to_identity_mapping() for job in self.jobs],
        }


@dataclass(frozen=True, slots=True)
class ParsedWorkflow:
    summary: WorkflowSummary
    facts: tuple[Fact, ...]
    unknowns: tuple[Unknown, ...]

    def __post_init__(self) -> None:
        if type(self.summary) is not WorkflowSummary:
            raise TypeError("parsed workflow requires an exact workflow summary")
        require_exact_tuple(self.facts, Fact, "workflow facts")
        require_exact_tuple(self.unknowns, Unknown, "workflow unknowns")
        require_canonical_evidence(self.facts, self.unknowns)
        require_predicate_closure(self.summary, self.facts, self.unknowns)


@dataclass(frozen=True, slots=True)
class WorkflowParseFailure:
    code: str
    unknown: Unknown

    def __post_init__(self) -> None:
        require_text(self.code, "parse failure code", maximum_bytes=128)
        if type(self.unknown) is not Unknown or self.unknown.field != "workflow.document":
            raise TypeError("parse failure requires a workflow.document unknown")


type WorkflowParseOutcome = ParsedWorkflow | WorkflowParseFailure


def workflow_subject_id(*, scope: RepositoryScope, revision: str, path: str) -> str:
    return (
        "workflow:"
        + hash_object(
            {
                "installationId": scope.installation_id,
                "repositoryId": scope.repository_id,
                "revision": revision,
                "path": path,
            }
        )[:32]
    )


def job_subject_id(*, workflow_id: str, job_id: str) -> str:
    return "job:" + hash_object({"workflowId": workflow_id, "jobId": job_id})[:32]


def require_predicate_closure(
    summary: WorkflowSummary,
    facts: tuple[Fact, ...],
    unknowns: tuple[Unknown, ...],
) -> None:
    outcomes = [(fact.subject_id, fact.field) for fact in facts]
    outcomes.extend((unknown.subject_id, unknown.field) for unknown in unknowns)
    if len(outcomes) != len(set(outcomes)):
        raise ValueError("one subject predicate cannot be both asserted and unknown")
    expected = {(summary.subject_id, field) for field in WORKFLOW_EXPECTED_PREDICATES}
    expected.update(
        (job.subject_id, field) for job in summary.jobs for field in JOB_EXPECTED_PREDICATES
    )
    subjects = {summary.subject_id, *(job.subject_id for job in summary.jobs)}
    actual = {(subject_id, field) for subject_id, field in outcomes if subject_id in subjects}
    if actual != expected:
        missing = sorted(expected - actual)
        surplus = sorted(actual - expected)
        raise ValueError(
            f"workflow predicate catalog is not closed: missing={missing}, surplus={surplus}"
        )
