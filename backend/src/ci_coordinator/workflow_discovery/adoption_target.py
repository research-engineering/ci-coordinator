"""Bounded target-owner evidence used only for workflow adoption assessment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Self

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    require_sha256,
    require_text,
    require_workflow_path,
)

type AdoptionTargetProjectionStatus = Literal["absent", "available", "invalid", "unavailable"]

_JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")


@dataclass(frozen=True, slots=True)
class AdoptionTargetJob:
    job_id: str
    needs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_job_id(self.job_id, "adoption target job")
        if (
            type(self.needs) is not tuple
            or any(
                type(value) is not str or _JOB_ID.fullmatch(value) is None for value in self.needs
            )
            or self.needs != tuple(sorted(set(self.needs), key=utf16_sort_key))
            or self.job_id in self.needs
        ):
            raise ValueError("adoption target job dependencies must be canonical")


@dataclass(frozen=True, slots=True)
class AdoptionTargetWorkflow:
    workflow_path: str
    execution_kind: Literal["witness-shards", "native-job-set"]
    execution_jobs: tuple[AdoptionTargetJob, ...]
    invocation_job_id: str
    plan_request_job_id: str
    plan_job_id: str
    fallback_job_id: str | None
    gate_job_id: str
    gate_signal_name: str

    def __post_init__(self) -> None:
        require_workflow_path(self.workflow_path)
        if self.execution_kind not in {"witness-shards", "native-job-set"}:
            raise ValueError("adoption target execution kind is not admitted")
        if type(self.execution_jobs) is not tuple or any(
            type(job) is not AdoptionTargetJob for job in self.execution_jobs
        ):
            raise TypeError("adoption target execution jobs must be exact")
        execution_job_ids = self.execution_job_ids
        if not execution_job_ids or execution_job_ids != tuple(
            sorted(set(execution_job_ids), key=utf16_sort_key)
        ):
            raise ValueError("adoption target execution jobs must be non-empty and canonical")
        _require_job_id(self.invocation_job_id, "adoption target invocation classifier job")
        _require_job_id(self.plan_request_job_id, "adoption target plan request job")
        _require_job_id(self.plan_job_id, "adoption target plan job")
        _require_job_id(self.gate_job_id, "adoption target gate job")
        if self.execution_kind == "witness-shards":
            _require_job_id(self.fallback_job_id, "adoption target fallback job")
        elif self.fallback_job_id is not None:
            raise ValueError("native adoption target cannot declare a separate fallback job")
        role_job_ids = (
            self.invocation_job_id,
            self.plan_request_job_id,
            self.plan_job_id,
            self.gate_job_id,
            *execution_job_ids,
            *(() if self.fallback_job_id is None else (self.fallback_job_id,)),
        )
        if len({job_id.lower() for job_id in role_job_ids}) != len(role_job_ids):
            raise ValueError("adoption target job roles must be distinct in GitHub expressions")
        known_dependencies = {*execution_job_ids, self.plan_job_id}
        if any(
            self.plan_job_id not in job.needs
            or any(dependency not in known_dependencies for dependency in job.needs)
            for job in self.execution_jobs
        ):
            raise ValueError("adoption target dependencies must close over plan and execution jobs")
        _require_acyclic(self.execution_jobs, self.plan_job_id)
        require_text(
            self.gate_signal_name,
            "adoption target gate signal",
            maximum_bytes=256,
        )

    @property
    def execution_job_ids(self) -> tuple[str, ...]:
        return tuple(job.job_id for job in self.execution_jobs)


@dataclass(frozen=True, slots=True)
class AdoptionTargetProjection:
    status: AdoptionTargetProjectionStatus
    registry_hash: str | None
    workflows: tuple[AdoptionTargetWorkflow, ...]

    @classmethod
    def absent(cls) -> Self:
        return cls("absent", None, ())

    @classmethod
    def failed(cls, status: Literal["invalid", "unavailable"]) -> Self:
        return cls(status, None, ())

    @classmethod
    def available(
        cls,
        registry_hash: str,
        workflows: tuple[AdoptionTargetWorkflow, ...],
    ) -> Self:
        return cls("available", registry_hash, workflows)

    def __post_init__(self) -> None:
        if self.status not in {"absent", "available", "invalid", "unavailable"}:
            raise ValueError("adoption target projection status is not admitted")
        if type(self.workflows) is not tuple or any(
            type(workflow) is not AdoptionTargetWorkflow for workflow in self.workflows
        ):
            raise TypeError("adoption target workflows must be exact")
        paths = tuple(workflow.workflow_path for workflow in self.workflows)
        if paths != tuple(sorted(set(paths), key=utf16_sort_key)):
            raise ValueError("adoption target workflows must be canonical")
        if self.status == "available":
            if self.registry_hash is None or not self.workflows:
                raise ValueError("available adoption target projection must be content-addressed")
            require_sha256(self.registry_hash, "adoption target registry hash")
        elif self.registry_hash is not None or self.workflows:
            raise ValueError("unavailable adoption target projection cannot carry authority")


def _require_job_id(value: object, field_name: str) -> None:
    if type(value) is not str or _JOB_ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} id must be canonical")


def _require_acyclic(
    jobs: tuple[AdoptionTargetJob, ...],
    plan_job_id: str,
) -> None:
    dependencies = {
        job.job_id: tuple(value for value in job.needs if value != plan_job_id) for job in jobs
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str) -> None:
        if job_id in visiting:
            raise ValueError("adoption target execution dependencies must be acyclic")
        if job_id in visited:
            return
        visiting.add(job_id)
        for dependency in dependencies[job_id]:
            if dependency in dependencies:
                visit(dependency)
        visiting.remove(job_id)
        visited.add(job_id)

    for job_id in dependencies:
        visit(job_id)
