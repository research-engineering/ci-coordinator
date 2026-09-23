"""Static target-job bindings admitted independently from hot policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.execution_orchestration.adapter_bundle import (
    MAX_TARGET_ADAPTER_FILES,
    TARGET_CONTROL_FILE_PATHS,
    TargetAdapterFileBinding,
)
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.validation_contract import (
    MAX_SERVICE_PROFILE_IDS,
    ExecutionProfile,
    ValidationCatalog,
)

_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")
_WORKFLOW_PATH = re.compile(r"\.github/workflows/[^/\\]+\.ya?ml")
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_GITHUB_OBJECT_ID = re.compile(r"[0-9a-f]{40}")
MAX_TARGET_SERVICE_PROFILE_IDS = MAX_SERVICE_PROFILE_IDS
CONTROL_INVOCATION_JOB_ID: Final = "ci-invocation"
LOCAL_PLAN_REQUEST_WORKFLOW_PATH: Final = ".github/workflows/trusted-plan-request.yml"
LOCAL_PLAN_REQUEST_WORKFLOW_REF: Final = f"$/{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}"
type ExecutionKind = Literal["witness-shards", "native-job-set"]


@dataclass(frozen=True, slots=True)
class TargetJobBinding:
    job_id: str
    needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.job_id) is not str or _JOB_ID.fullmatch(self.job_id) is None:
            raise ValueError("target job id must be canonical")
        if (
            type(self.needs) is not tuple
            or any(
                type(value) is not str or _JOB_ID.fullmatch(value) is None for value in self.needs
            )
            or tuple(sorted(set(self.needs), key=utf16_sort_key)) != self.needs
        ):
            raise ValueError("target job dependencies must be canonical")
        if self.job_id in self.needs:
            raise ValueError("target job cannot depend on itself")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"jobId": self.job_id, "needs": list(self.needs)}


@dataclass(frozen=True, slots=True)
class TargetWorkflowBinding:
    workflow_path: str
    execution_kind: ExecutionKind
    execution_jobs: tuple[TargetJobBinding, ...]
    plan_request_job_id: str
    plan_request_workflow_ref: str
    plan_job_id: str
    fallback_job_id: str | None
    gate_job_id: str
    gate_signal_name: str
    required_job_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_workflow_path(self.workflow_path)
        if self.execution_kind not in {"witness-shards", "native-job-set"}:
            raise ValueError("target workflow execution kind is not admitted")
        if (
            type(self.execution_jobs) is not tuple
            or not self.execution_jobs
            or any(type(value) is not TargetJobBinding for value in self.execution_jobs)
            or tuple(
                sorted(
                    {value.job_id for value in self.execution_jobs},
                    key=utf16_sort_key,
                )
            )
            != self.execution_job_ids
        ):
            raise ValueError("target workflow execution jobs must be non-empty and canonical")
        for name, value in (
            ("plan request", self.plan_request_job_id),
            ("plan", self.plan_job_id),
            ("gate", self.gate_job_id),
        ):
            if type(value) is not str or _JOB_ID.fullmatch(value) is None:
                raise ValueError(f"target workflow {name} job id must be canonical")
        if (
            self.plan_request_workflow_ref != LOCAL_PLAN_REQUEST_WORKFLOW_REF
            and _external_reusable_workflow_revision(self.plan_request_workflow_ref) is None
        ):
            raise ValueError("target workflow plan requester must use an immutable workflow ref")
        if self.execution_kind == "witness-shards":
            if (
                type(self.fallback_job_id) is not str
                or _JOB_ID.fullmatch(self.fallback_job_id) is None
            ):
                raise ValueError("sharded target workflow requires a canonical fallback job")
        elif self.fallback_job_id is not None:
            raise ValueError("native target workflow cannot declare a separate fallback job")
        role_job_ids = (
            CONTROL_INVOCATION_JOB_ID,
            self.plan_request_job_id,
            self.plan_job_id,
            self.gate_job_id,
            *self.execution_job_ids,
            *(() if self.fallback_job_id is None else (self.fallback_job_id,)),
        )
        if len({_github_expression_key(job_id) for job_id in role_job_ids}) != len(role_job_ids):
            raise ValueError("target workflow job roles must be distinct in GitHub expressions")
        execution_job_ids = set(self.execution_job_ids)
        if (
            type(self.required_job_ids) is not tuple
            or tuple(sorted(set(self.required_job_ids), key=utf16_sort_key))
            != self.required_job_ids
            or any(job_id not in execution_job_ids for job_id in self.required_job_ids)
        ):
            raise ValueError("required target jobs must be canonical execution jobs")
        if any(
            self.plan_job_id not in job.needs
            or any(
                dependency != self.plan_job_id and dependency not in execution_job_ids
                for dependency in job.needs
            )
            for job in self.execution_jobs
        ):
            raise ValueError(
                "target workflow dependencies must close over its plan and execution jobs"
            )
        _require_acyclic_jobs(self.execution_jobs)
        ProviderSignal.declared_native(
            workflow_path=self.workflow_path,
            job_id=self.gate_job_id,
            job_name=self.gate_signal_name,
        )

    @property
    def execution_job_ids(self) -> tuple[str, ...]:
        return tuple(job.job_id for job in self.execution_jobs)

    @property
    def invocation_job_id(self) -> str:
        return CONTROL_INVOCATION_JOB_ID

    @property
    def gate_dependencies(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    self.plan_job_id,
                    *self.execution_job_ids,
                    *(() if self.fallback_job_id is None else (self.fallback_job_id,)),
                },
                key=utf16_sort_key,
            )
        )

    @property
    def job_topology(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        topology = [
            (CONTROL_INVOCATION_JOB_ID, ()),
            (self.plan_request_job_id, (CONTROL_INVOCATION_JOB_ID,)),
            (self.plan_job_id, (self.plan_request_job_id,)),
            *((job.job_id, job.needs) for job in self.execution_jobs),
            (self.gate_job_id, self.gate_dependencies),
        ]
        if self.fallback_job_id is not None:
            topology.append((self.fallback_job_id, (self.plan_job_id,)))
        return tuple(sorted(topology, key=lambda item: utf16_sort_key(item[0])))

    @property
    def provider_signal(self) -> ProviderSignal:
        return ProviderSignal.declared_native(
            workflow_path=self.workflow_path,
            job_id=self.gate_job_id,
            job_name=self.gate_signal_name,
        )

    def admits_plan_request_identity(
        self,
        *,
        job_workflow_ref: str | None,
        job_workflow_sha: str | None,
        repository: str | None = None,
        workflow_ref: str | None = None,
        workflow_sha: str | None = None,
    ) -> bool:
        if self.plan_request_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF:
            if repository is None or workflow_ref is None or workflow_sha is None:
                return False
            prefix = f"{repository}/{self.workflow_path}@"
            if not workflow_ref.startswith(prefix):
                return False
            ref = workflow_ref[len(prefix) :]
            return (
                ref.startswith("refs/")
                and len(ref) > len("refs/")
                and _GITHUB_OBJECT_ID.fullmatch(workflow_sha) is not None
                and job_workflow_ref == f"{repository}/{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}@{ref}"
                and job_workflow_sha == workflow_sha
            )
        revision = _external_reusable_workflow_revision(self.plan_request_workflow_ref)
        return (
            revision is not None
            and job_workflow_ref == self.plan_request_workflow_ref
            and job_workflow_sha == revision
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "workflowPath": self.workflow_path,
            "executionKind": self.execution_kind,
            "executionJobs": [job.to_identity_mapping() for job in self.execution_jobs],
            "planRequestJobId": self.plan_request_job_id,
            "planRequestWorkflowRef": self.plan_request_workflow_ref,
            "planJobId": self.plan_job_id,
            "fallbackJobId": self.fallback_job_id,
            "gateJobId": self.gate_job_id,
            "gateSignalName": self.gate_signal_name,
            "requiredJobIds": list(self.required_job_ids),
        }

    def dependency_closure(self, job_ids: tuple[str, ...]) -> tuple[str, ...]:
        if (
            type(job_ids) is not tuple
            or not job_ids
            or tuple(sorted(set(job_ids), key=utf16_sort_key)) != job_ids
            or any(job_id not in self.execution_job_ids for job_id in job_ids)
        ):
            raise ValueError("dependency closure roots must be admitted and canonical")
        selected = set(job_ids)
        dependencies = {job.job_id: job.needs for job in self.execution_jobs}
        pending = list(selected)
        while pending:
            job_id = pending.pop()
            for dependency in dependencies[job_id]:
                if dependency in dependencies and dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
        return tuple(sorted(selected, key=utf16_sort_key))


@dataclass(frozen=True, slots=True)
class TargetProfileBinding:
    profile_id: str
    workflow_path: str
    job_id: str
    runner_profile_id: str
    permission_profile_id: str
    credential_profile_id: str
    fixture_profile_id: str
    service_profile_ids: tuple[str, ...]
    capacity_class_id: str
    execution_kind: ExecutionKind = "witness-shards"

    def __post_init__(self) -> None:
        for name, value in (
            ("profile_id", self.profile_id),
            ("runner_profile_id", self.runner_profile_id),
            ("permission_profile_id", self.permission_profile_id),
            ("credential_profile_id", self.credential_profile_id),
            ("fixture_profile_id", self.fixture_profile_id),
            ("capacity_class_id", self.capacity_class_id),
        ):
            if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{name} must be a canonical identifier")
        _require_workflow_path(self.workflow_path)
        if type(self.job_id) is not str or _JOB_ID.fullmatch(self.job_id) is None:
            raise ValueError("job_id must be a canonical static job identifier")
        if self.execution_kind not in {"witness-shards", "native-job-set"}:
            raise ValueError("target profile execution kind is not admitted")
        if len(self.service_profile_ids) > MAX_TARGET_SERVICE_PROFILE_IDS:
            raise ValueError("service_profile_ids exceeds the admitted maximum")
        if any(_IDENTIFIER.fullmatch(value) is None for value in self.service_profile_ids):
            raise ValueError("service_profile_ids must contain canonical identifiers")
        if tuple(sorted(set(self.service_profile_ids), key=utf16_sort_key)) != (
            self.service_profile_ids
        ):
            raise ValueError("service_profile_ids must be canonical")

    @classmethod
    def from_profile(
        cls,
        profile: ExecutionProfile,
        *,
        workflow_path: str,
        job_id: str,
        execution_kind: ExecutionKind = "witness-shards",
    ) -> TargetProfileBinding:
        if type(profile) is not ExecutionProfile:
            raise TypeError("target binding requires an exact execution profile")
        return cls(
            profile_id=profile.profile_id,
            workflow_path=workflow_path,
            job_id=job_id,
            runner_profile_id=profile.runner_profile_id,
            permission_profile_id=profile.permission_profile_id,
            credential_profile_id=profile.credential_profile_id,
            fixture_profile_id=profile.fixture_profile_id,
            service_profile_ids=profile.service_profile_ids,
            capacity_class_id=profile.capacity_class_id,
            execution_kind=execution_kind,
        )

    def matches(self, profile: ExecutionProfile) -> bool:
        return (
            self.profile_id,
            self.runner_profile_id,
            self.permission_profile_id,
            self.credential_profile_id,
            self.fixture_profile_id,
            self.service_profile_ids,
            self.capacity_class_id,
        ) == (
            profile.profile_id,
            profile.runner_profile_id,
            profile.permission_profile_id,
            profile.credential_profile_id,
            profile.fixture_profile_id,
            profile.service_profile_ids,
            profile.capacity_class_id,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "profileId": self.profile_id,
            "workflowPath": self.workflow_path,
            "jobId": self.job_id,
            "executionKind": self.execution_kind,
            "runnerProfileId": self.runner_profile_id,
            "permissionProfileId": self.permission_profile_id,
            "credentialProfileId": self.credential_profile_id,
            "fixtureProfileId": self.fixture_profile_id,
            "serviceProfileIds": list(self.service_profile_ids),
            "capacityClassId": self.capacity_class_id,
        }


@dataclass(frozen=True, slots=True)
class TargetExecutionRegistry:
    generator_id: str
    generator_version: str
    adapter_files: tuple[TargetAdapterFileBinding, ...]
    workflows: tuple[TargetWorkflowBinding, ...]
    profiles: tuple[TargetProfileBinding, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("generator_id", self.generator_id),
            ("generator_version", self.generator_version),
        ):
            if type(value) is not str or not value or len(value.encode("utf-8")) > 128:
                raise ValueError(f"{name} must be bounded non-empty text")
        if (
            type(self.adapter_files) is not tuple
            or not 1 <= len(self.adapter_files) <= MAX_TARGET_ADAPTER_FILES
            or any(type(item) is not TargetAdapterFileBinding for item in self.adapter_files)
        ):
            raise TypeError("target registry adapter files must be a bounded exact tuple")
        adapter_paths = tuple(item.path for item in self.adapter_files)
        if tuple(sorted(set(adapter_paths), key=utf16_sort_key)) != adapter_paths:
            raise ValueError("target registry adapter files must be canonical")
        control_paths = {item.path for item in self.adapter_files if not item.is_workflow}
        if control_paths != set(TARGET_CONTROL_FILE_PATHS):
            raise ValueError("target registry must bind the fixed control-file set")
        if type(self.workflows) is not tuple or any(
            type(item) is not TargetWorkflowBinding for item in self.workflows
        ):
            raise TypeError("target registry workflows must be exact")
        workflow_paths = tuple(item.workflow_path for item in self.workflows)
        if (
            not workflow_paths
            or tuple(sorted(set(workflow_paths), key=utf16_sort_key)) != workflow_paths
        ):
            raise ValueError("target registry workflows must be non-empty and canonical")
        adapter_workflow_paths = {item.path for item in self.adapter_files if item.is_workflow}
        if not set(workflow_paths).issubset(adapter_workflow_paths):
            raise ValueError("target registry workflows must belong to the adapter bundle")
        if (
            any(
                item.plan_request_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF
                for item in self.workflows
            )
            and LOCAL_PLAN_REQUEST_WORKFLOW_PATH not in adapter_workflow_paths
        ):
            raise ValueError("local plan requester must belong to the adapter bundle")
        if type(self.profiles) is not tuple or any(
            type(item) is not TargetProfileBinding for item in self.profiles
        ):
            raise TypeError("target registry profiles must be exact")
        profile_ids = tuple(item.profile_id for item in self.profiles)
        if not profile_ids or tuple(sorted(set(profile_ids), key=utf16_sort_key)) != profile_ids:
            raise ValueError("target registry profiles must be non-empty and canonical")
        static_jobs = tuple((item.workflow_path, item.job_id) for item in self.profiles)
        if len(static_jobs) != len(set(static_jobs)):
            raise ValueError("each target profile must own a distinct static job")
        profiles_by_workflow: dict[str, list[TargetProfileBinding]] = {
            workflow_path: [] for workflow_path in workflow_paths
        }
        for profile in self.profiles:
            try:
                profiles_by_workflow[profile.workflow_path].append(profile)
            except KeyError as error:
                raise ValueError("target profile references an unknown workflow") from error
        for workflow in self.workflows:
            profiles = profiles_by_workflow[workflow.workflow_path]
            if not profiles:
                raise ValueError("target workflow must own at least one execution profile")
            expected_jobs = tuple(
                sorted((profile.job_id for profile in profiles), key=utf16_sort_key)
            )
            if workflow.execution_job_ids != expected_jobs:
                raise ValueError("target workflow execution jobs do not match its profiles")
            if any(profile.execution_kind != workflow.execution_kind for profile in profiles):
                raise ValueError("target workflow and profile execution kinds must match")

    @property
    def registry_hash(self) -> str:
        return hash_object(self.to_identity_mapping())

    def admits(self, catalog: ValidationCatalog) -> bool:
        if type(catalog) is not ValidationCatalog:
            return False
        binding_by_id = {item.profile_id: item for item in self.profiles}
        catalog_by_id = {item.profile_id: item for item in catalog.execution_profiles}
        return binding_by_id.keys() == catalog_by_id.keys() and all(
            binding_by_id[profile.profile_id].matches(profile)
            for profile in catalog.execution_profiles
        )

    def workflow(self, workflow_path: str) -> TargetWorkflowBinding | None:
        return next(
            (item for item in self.workflows if item.workflow_path == workflow_path),
            None,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "dynamic-ci-target-execution-registry/v1",
            "generator": {"id": self.generator_id, "version": self.generator_version},
            "adapterFiles": [item.to_identity_mapping() for item in self.adapter_files],
            "workflows": [item.to_identity_mapping() for item in self.workflows],
            "profiles": [item.to_identity_mapping() for item in self.profiles],
        }


@dataclass(frozen=True, slots=True)
class TrustedExecutionTarget:
    verified_plan_id: str
    workflow_path: str
    selected_profile_ids: tuple[str, ...]
    target_registry: TargetExecutionRegistry

    def __post_init__(self) -> None:
        if type(self.verified_plan_id) is not str or not self.verified_plan_id:
            raise ValueError("execution target verified plan id must be non-empty")
        if (
            type(self.selected_profile_ids) is not tuple
            or not self.selected_profile_ids
            or tuple(sorted(set(self.selected_profile_ids), key=utf16_sort_key))
            != self.selected_profile_ids
        ):
            raise ValueError("execution target profiles must be non-empty and canonical")
        if type(self.target_registry) is not TargetExecutionRegistry:
            raise TypeError("execution target requires an exact target registry")
        workflow = self.target_registry.workflow(self.workflow_path)
        if workflow is None:
            raise ValueError("execution target workflow is not admitted by the registry")
        bindings = {
            item.profile_id: item
            for item in self.target_registry.profiles
            if item.workflow_path == self.workflow_path
        }
        if any(
            profile_id not in bindings
            or bindings[profile_id].execution_kind != workflow.execution_kind
            for profile_id in self.selected_profile_ids
        ):
            raise ValueError("execution target profile is outside its workflow authority")
        selected_job_ids = tuple(
            sorted(
                {bindings[profile_id].job_id for profile_id in self.selected_profile_ids},
                key=utf16_sort_key,
            )
        )
        dependency_closure = workflow.dependency_closure(selected_job_ids)
        if workflow.execution_kind == "witness-shards" and dependency_closure != selected_job_ids:
            raise ValueError("witness-shard target jobs must be dependency closed")
        executable_job_ids = (
            dependency_closure if workflow.execution_kind == "native-job-set" else selected_job_ids
        )
        if any(job_id not in executable_job_ids for job_id in workflow.required_job_ids):
            raise ValueError("execution target omits a required static job")

    @property
    def execution_kind(self) -> ExecutionKind:
        return self._workflow.execution_kind

    @property
    def provider_signal(self) -> ProviderSignal:
        return self._workflow.provider_signal

    @property
    def target_registry_hash(self) -> str:
        return self.target_registry.registry_hash

    def admits_plan_request_identity(
        self,
        *,
        job_workflow_ref: str | None,
        job_workflow_sha: str | None,
        repository: str | None = None,
        workflow_ref: str | None = None,
        workflow_sha: str | None = None,
    ) -> bool:
        return self._workflow.admits_plan_request_identity(
            job_workflow_ref=job_workflow_ref,
            job_workflow_sha=job_workflow_sha,
            repository=repository,
            workflow_ref=workflow_ref,
            workflow_sha=workflow_sha,
        )

    @property
    def _workflow(self) -> TargetWorkflowBinding:
        workflow = self.target_registry.workflow(self.workflow_path)
        if workflow is None:  # pragma: no cover - guarded by construction
            raise RuntimeError("execution target workflow disappeared")
        return workflow


def _require_workflow_path(value: object) -> None:
    if (
        type(value) is not str
        or _WORKFLOW_PATH.fullmatch(value) is None
        or len(value.encode("utf-8")) > 256
    ):
        raise ValueError("workflow_path must identify a bounded workflow file")


def _github_expression_key(job_id: str) -> str:
    return job_id.lower()


def _external_reusable_workflow_revision(value: object) -> str | None:
    if type(value) is not str or len(value.encode("utf-8")) > 512:
        return None
    target, separator, revision = value.rpartition("@")
    parts = target.split("/", 2)
    if (
        separator != "@"
        or len(parts) != 3
        or _GITHUB_OWNER.fullmatch(parts[0]) is None
        or _GITHUB_REPOSITORY.fullmatch(parts[1]) is None
        or _WORKFLOW_PATH.fullmatch(parts[2]) is None
        or _GITHUB_OBJECT_ID.fullmatch(revision) is None
    ):
        return None
    return revision


def _require_acyclic_jobs(jobs: tuple[TargetJobBinding, ...]) -> None:
    dependencies = {job.job_id: job.needs for job in jobs}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str) -> None:
        if job_id in visiting:
            raise ValueError("target workflow execution dependencies must be acyclic")
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
