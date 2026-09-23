"""Provider-observed workflow capabilities used for execution admission."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.git_reference import git_branch_name_is_admitted
from ci_coordinator.kernel.hashing import hash_object
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.repo_context.runner_selector import (
    StaticRunnerSelector,
    WorkflowJobRunnerSelector,
)
from ci_coordinator.repo_context.workflow_control_plane import (
    admitted_control_job_projection_hashes,
)


@dataclass(frozen=True, slots=True)
class WorkflowJobAuthority:
    job_id: str
    control_projection_hash: str
    condition_contexts: tuple[str, ...]
    declares_continue_on_error: bool

    def __post_init__(self) -> None:
        if type(self.job_id) is not str or not self.job_id:
            raise ValueError("workflow authority job id must be non-empty")
        if (
            type(self.control_projection_hash) is not str
            or len(self.control_projection_hash) != 64
            or any(
                character not in "0123456789abcdef" for character in self.control_projection_hash
            )
        ):
            raise ValueError("workflow control projection hash must be lowercase SHA-256")
        _require_canonical_text(
            self.condition_contexts,
            field_name="job condition contexts",
            allow_empty=True,
        )
        if type(self.declares_continue_on_error) is not bool:
            raise TypeError("workflow continue-on-error flag must be an exact boolean")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "controlProjectionHash": self.control_projection_hash,
            "conditionContexts": list(self.condition_contexts),
            "declaresContinueOnError": self.declares_continue_on_error,
        }


@dataclass(frozen=True, slots=True)
class DefaultBranchWorkflow:
    workflow_id: int
    path: str
    active: bool

    def __post_init__(self) -> None:
        if type(self.workflow_id) is not int or self.workflow_id < 1:
            raise ValueError("workflow_id must be a positive integer")
        if not is_workflow_path(self.path):
            raise ValueError("path must identify a bounded workflow file")
        if type(self.active) is not bool:
            raise TypeError("active must be an exact boolean")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"workflowId": self.workflow_id, "path": self.path, "active": self.active}


@dataclass(frozen=True, slots=True)
class RevisionWorkflowCapability:
    path: str
    revision_sha: str
    job_ids: tuple[str, ...]
    job_needs: tuple[tuple[str, tuple[str, ...]], ...]
    provider_job_names: tuple[tuple[str, str], ...]
    always_job_ids: tuple[str, ...]
    triggers: tuple[str, ...]
    local_reusable_workflow_paths: tuple[str, ...]
    job_authorities: tuple[WorkflowJobAuthority, ...]
    declares_workflow_environment: bool
    declares_workflow_defaults: bool
    job_runner_selectors: tuple[WorkflowJobRunnerSelector, ...] = ()

    def __post_init__(self) -> None:
        if not is_workflow_path(self.path):
            raise ValueError("path must identify a bounded workflow file")
        if not is_git_object_revision(self.revision_sha):
            raise ValueError("revision_sha must be lowercase Git object hexadecimal")
        _require_canonical_text(self.job_ids, field_name="job_ids", allow_empty=False)
        _require_job_needs(self.job_needs, job_ids=self.job_ids)
        _require_provider_job_names(self.provider_job_names, job_ids=self.job_ids)
        _require_canonical_text(
            self.always_job_ids,
            field_name="always_job_ids",
            allow_empty=True,
        )
        if any(job_id not in self.job_ids for job_id in self.always_job_ids):
            raise ValueError("always_job_ids contains an unknown job")
        _require_canonical_text(self.triggers, field_name="triggers", allow_empty=False)
        if (
            type(self.local_reusable_workflow_paths) is not tuple
            or tuple(sorted(set(self.local_reusable_workflow_paths), key=utf16_sort_key))
            != self.local_reusable_workflow_paths
            or any(not is_workflow_path(path) for path in self.local_reusable_workflow_paths)
        ):
            raise ValueError("local reusable workflow paths must be canonical")
        if type(self.job_authorities) is not tuple or any(
            type(item) is not WorkflowJobAuthority for item in self.job_authorities
        ):
            raise TypeError("workflow job authorities must be exact")
        if tuple(item.job_id for item in self.job_authorities) != self.job_ids:
            raise ValueError("workflow job authorities must cover every job canonically")
        if type(self.declares_workflow_environment) is not bool:
            raise TypeError("workflow environment flag must be an exact boolean")
        if type(self.declares_workflow_defaults) is not bool:
            raise TypeError("workflow defaults flag must be an exact boolean")
        if type(self.job_runner_selectors) is not tuple or any(
            type(item) is not WorkflowJobRunnerSelector for item in self.job_runner_selectors
        ):
            raise TypeError("workflow runner selectors must be exact")
        selector_job_ids = tuple(item.job_id for item in self.job_runner_selectors)
        if tuple(sorted(set(selector_job_ids), key=utf16_sort_key)) != selector_job_ids or any(
            job_id not in self.job_ids for job_id in selector_job_ids
        ):
            raise ValueError("workflow runner selectors must be canonical known jobs")

    @property
    def supports_dispatch(self) -> bool:
        return "workflow_dispatch" in self.triggers

    @property
    def supports_reuse(self) -> bool:
        return "workflow_call" in self.triggers

    def runner_selector(self, job_id: str) -> StaticRunnerSelector | None:
        return next(
            (item.selector for item in self.job_runner_selectors if item.job_id == job_id),
            None,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {"revision": self.revision_sha, **self.to_stable_mapping()}

    def to_stable_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "jobIds": list(self.job_ids),
            "jobNeeds": [[job_id, list(needs)] for job_id, needs in self.job_needs],
            "providerJobNames": [list(item) for item in self.provider_job_names],
            "alwaysJobIds": list(self.always_job_ids),
            "triggers": list(self.triggers),
            "localReusableWorkflowPaths": list(self.local_reusable_workflow_paths),
            "jobAuthorities": [item.to_identity_mapping() for item in self.job_authorities],
            "declaresWorkflowEnvironment": self.declares_workflow_environment,
            "declaresWorkflowDefaults": self.declares_workflow_defaults,
            "jobRunnerSelectors": [
                item.to_identity_mapping() for item in self.job_runner_selectors
            ],
        }


@dataclass(frozen=True, slots=True)
class ProviderWorkflowInventory:
    """Optional default-branch index plus exact-revision workflow capabilities."""

    revision_sha: str
    default_branch_workflows: tuple[DefaultBranchWorkflow, ...]
    revision_capabilities: tuple[RevisionWorkflowCapability, ...]

    def __post_init__(self) -> None:
        if not is_git_object_revision(self.revision_sha):
            raise ValueError("revision_sha must be lowercase Git object hexadecimal")
        if type(self.default_branch_workflows) is not tuple or any(
            type(item) is not DefaultBranchWorkflow for item in self.default_branch_workflows
        ):
            raise TypeError("default_branch_workflows must contain exact workflow entries")
        if type(self.revision_capabilities) is not tuple or any(
            type(item) is not RevisionWorkflowCapability for item in self.revision_capabilities
        ):
            raise TypeError("revision_capabilities must contain exact workflow capabilities")

        default_paths = tuple(item.path for item in self.default_branch_workflows)
        capability_paths = tuple(item.path for item in self.revision_capabilities)
        if tuple(sorted(set(default_paths), key=utf16_sort_key)) != default_paths:
            raise ValueError("default-branch workflow paths must be canonical")
        if len({item.workflow_id for item in self.default_branch_workflows}) != len(
            self.default_branch_workflows
        ):
            raise ValueError("default-branch workflow ids must be unique")
        if tuple(sorted(set(capability_paths), key=utf16_sort_key)) != capability_paths:
            raise ValueError("revision capability paths must be canonical")
        if any(item.revision_sha != self.revision_sha for item in self.revision_capabilities):
            raise ValueError("all workflow capabilities must share the inventory revision")

    def admits_static_job(self, *, workflow_path: str, job_id: str) -> bool:
        """Require default-branch activation and an exact-revision static job."""

        active_paths = {item.path for item in self.default_branch_workflows if item.active}
        capabilities = {item.path: item for item in self.revision_capabilities}
        capability = capabilities.get(workflow_path)
        return (
            workflow_path in active_paths
            and capability is not None
            and job_id in capability.job_ids
        )

    def admits_revision_static_job(self, *, workflow_path: str, job_id: str) -> bool:
        """Require only an exact-revision static job from an authenticated run."""

        capabilities = {item.path: item for item in self.revision_capabilities}
        capability = capabilities.get(workflow_path)
        return capability is not None and job_id in capability.job_ids

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "revision": self.revision_sha,
            **self.to_stable_mapping(),
        }

    def to_stable_mapping(self) -> dict[str, object]:
        return {
            "defaultBranchWorkflows": [
                item.to_identity_mapping() for item in self.default_branch_workflows
            ],
            "revisionCapabilities": [
                item.to_stable_mapping() for item in self.revision_capabilities
            ],
        }

    def admits_static_gate(
        self,
        *,
        workflow_path: str,
        job_id: str,
        job_name: str,
        required_dependencies: tuple[str, ...],
        require_default_branch_activation: bool = True,
    ) -> bool:
        """Require one exact always-run aggregate over every protected dependency."""

        job_admitted = (
            self.admits_static_job
            if require_default_branch_activation
            else self.admits_revision_static_job
        )
        if not job_admitted(workflow_path=workflow_path, job_id=job_id):
            return False
        capability = next(item for item in self.revision_capabilities if item.path == workflow_path)
        actual_needs = dict(capability.job_needs)[job_id]
        return (
            type(required_dependencies) is tuple
            and bool(required_dependencies)
            and tuple(sorted(set(required_dependencies), key=utf16_sort_key))
            == required_dependencies
            and all(dependency in capability.job_ids for dependency in required_dependencies)
            and required_dependencies == actual_needs
            and job_id in capability.always_job_ids
            and (job_id, job_name) in capability.provider_job_names
            and sum(observed_name == job_name for _, observed_name in capability.provider_job_names)
            == 1
        )

    def admits_exact_job_topology(
        self,
        *,
        workflow_path: str,
        expected: tuple[tuple[str, tuple[str, ...]], ...],
        require_default_branch_activation: bool = True,
    ) -> bool:
        """Bind every workflow job and its complete direct dependency set."""

        if type(expected) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not tuple
            for item in expected
        ):
            return False
        expected_job_ids = tuple(job_id for job_id, _ in expected)
        if (
            not expected_job_ids
            or tuple(sorted(set(expected_job_ids), key=utf16_sort_key)) != expected_job_ids
            or not (
                self.admits_static_job
                if require_default_branch_activation
                else self.admits_revision_static_job
            )(
                workflow_path=workflow_path,
                job_id=expected_job_ids[0],
            )
        ):
            return False
        capability = next(item for item in self.revision_capabilities if item.path == workflow_path)
        if capability.job_ids != expected_job_ids:
            return False
        known_job_ids = set(capability.job_ids)
        if any(
            tuple(sorted(set(dependencies), key=utf16_sort_key)) != dependencies
            or any(
                type(dependency) is not str or dependency not in known_job_ids
                for dependency in dependencies
            )
            for _, dependencies in expected
        ):
            return False
        return capability.job_needs == expected

    def admits_local_reusable_workflow_closure(
        self,
        *,
        root_paths: tuple[str, ...],
        expected_paths: tuple[str, ...],
    ) -> bool:
        """Require the exact acyclic same-revision local workflow call closure."""

        if (
            type(root_paths) is not tuple
            or type(expected_paths) is not tuple
            or not root_paths
            or tuple(sorted(set(root_paths), key=utf16_sort_key)) != root_paths
            or tuple(sorted(set(expected_paths), key=utf16_sort_key)) != expected_paths
            or not set(root_paths).issubset(expected_paths)
        ):
            return False
        capabilities = {item.path: item for item in self.revision_capabilities}
        if set(capabilities) != set(expected_paths):
            return False

        reachable: set[str] = set()

        def visit(
            path: str,
            depth: int,
            root_seen: set[str],
            root_visiting: set[str],
        ) -> bool:
            if depth > 10 or len(root_seen) > 51:
                return False
            if path in root_visiting:
                return False
            if path in root_seen:
                return True
            capability = capabilities.get(path)
            if (
                capability is None
                or capability.declares_workflow_environment
                or capability.declares_workflow_defaults
                or any(
                    authority.declares_continue_on_error for authority in capability.job_authorities
                )
            ):
                return False
            root_seen.add(path)
            reachable.add(path)
            if len(root_seen) > 51:
                return False
            root_visiting.add(path)
            for target in capability.local_reusable_workflow_paths:
                target_capability = capabilities.get(target)
                if (
                    target_capability is None
                    or not target_capability.supports_reuse
                    or not visit(target, depth + 1, root_seen, root_visiting)
                ):
                    return False
            root_visiting.remove(path)
            return True

        for path in root_paths:
            if not visit(path, 1, set(), set()):
                return False
        return reachable == set(expected_paths)

    def admits_control_plane(
        self,
        *,
        workflow_path: str,
        execution_kind: str,
        invocation_job_id: str,
        plan_request_job_id: str,
        plan_request_workflow_ref: str,
        plan_job_id: str,
        fallback_job_id: str | None,
        gate_job_id: str,
        execution_job_ids: tuple[str, ...],
    ) -> bool:
        """Require the generated control jobs and run-bound route contexts."""

        capabilities = {item.path: item for item in self.revision_capabilities}
        capability = capabilities.get(workflow_path)
        if capability is None:
            return False
        authorities = {item.job_id: item for item in capability.job_authorities}
        invocation = authorities.get(invocation_job_id)
        plan_request = authorities.get(plan_request_job_id)
        plan = authorities.get(plan_job_id)
        gate = authorities.get(gate_job_id)
        if invocation is None or plan_request is None or plan is None or gate is None:
            return False
        admitted = admitted_control_job_projection_hashes(
            workflow_path=workflow_path,
            workflow_triggers=capability.triggers,
            execution_kind=execution_kind,
            execution_job_ids=execution_job_ids,
            invocation_job_id=invocation_job_id,
            plan_request_job_id=plan_request_job_id,
            plan_request_workflow_ref=plan_request_workflow_ref,
            plan_job_id=plan_job_id,
            gate_job_id=gate_job_id,
            fallback_job_id=fallback_job_id,
        )
        if (
            admitted is None
            or (
                invocation.control_projection_hash,
                plan_request.control_projection_hash,
                plan.control_projection_hash,
                gate.control_projection_hash,
            )
            not in admitted
        ):
            return False
        needs = dict(capability.job_needs)
        gate_dependencies = tuple(
            sorted(
                {
                    plan_job_id,
                    *execution_job_ids,
                    *(() if fallback_job_id is None else (fallback_job_id,)),
                },
                key=utf16_sort_key,
            )
        )
        if (
            needs.get(invocation_job_id) != ()
            or needs.get(plan_request_job_id) != (invocation_job_id,)
            or needs.get(plan_job_id) != (plan_request_job_id,)
            or needs.get(gate_job_id) != gate_dependencies
            or (fallback_job_id is not None and needs.get(fallback_job_id) != (plan_job_id,))
        ):
            return False
        protected_job_ids = {
            invocation_job_id,
            plan_request_job_id,
            plan_job_id,
            gate_job_id,
            *execution_job_ids,
            *(() if fallback_job_id is None else (fallback_job_id,)),
        }
        if any(
            job_id not in authorities or authorities[job_id].declares_continue_on_error
            for job_id in protected_job_ids
        ):
            return False
        route_contexts = {"github", "needs"}
        route_job_ids = (
            *execution_job_ids,
            *(() if fallback_job_id is None else (fallback_job_id,)),
        )
        return all(
            job_id in authorities
            and set(authorities[job_id].condition_contexts).issubset(route_contexts)
            for job_id in route_job_ids
        )


@dataclass(frozen=True, slots=True)
class ProviderWorkflowInventoryEvidence:
    """Subject-bound provider evidence around a workflow inventory value."""

    scope: RepositoryScope
    owner: str
    name: str
    default_branch: str
    api_version: str
    inventory: ProviderWorkflowInventory

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("workflow inventory evidence requires an exact repository scope")
        for value, label, maximum in (
            (self.owner, "repository owner", 512),
            (self.name, "repository name", 512),
            (self.default_branch, "default branch", 1_024),
            (self.api_version, "provider API version", 64),
        ):
            _require_scalar_text(value, field_name=label, maximum_bytes=maximum)
        if any(
            value in {".", ".."} or "/" in value or "\0" in value
            for value in (self.owner, self.name)
        ):
            raise ValueError("workflow inventory repository components are invalid")
        if not git_branch_name_is_admitted(self.default_branch):
            raise ValueError("workflow inventory default branch is invalid")
        if type(self.inventory) is not ProviderWorkflowInventory:
            raise TypeError("workflow inventory evidence requires an exact inventory")

    @property
    def evidence_digest(self) -> str:
        return hash_object(self.to_identity_mapping())

    @property
    def stable_authority_digest(self) -> str:
        return hash_object(self.to_stable_mapping())

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            **self._subject_mapping(),
            "inventory": self.inventory.to_identity_mapping(),
        }

    def to_stable_mapping(self) -> dict[str, object]:
        return {
            **self._subject_mapping(),
            "inventory": self.inventory.to_stable_mapping(),
        }

    def _subject_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "github-provider-workflow-inventory-evidence/v1",
            "scope": {
                "installationId": self.scope.installation_id,
                "repositoryId": self.scope.repository_id,
            },
            "repository": {
                "owner": self.owner,
                "name": self.name,
                "defaultBranch": self.default_branch,
            },
            "apiVersion": self.api_version,
        }


def is_git_object_revision(value: object) -> bool:
    return (
        type(value) is str
        and 40 <= len(value) <= 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_scalar_text(value: object, *, field_name: str, maximum_bytes: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{field_name} must be bounded canonical text")
    return value


def is_workflow_path(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith(".github/workflows/")
        and value.endswith((".yml", ".yaml"))
        and value.count("/") == 2
        and len(value.encode("utf-8")) <= 256
        and ".." not in value.split("/")
        and "\\" not in value
    )


def _require_canonical_text(
    values: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool,
) -> None:
    if type(values) is not tuple or (not allow_empty and not values):
        raise ValueError(f"{field_name} must be a non-empty tuple")
    if any(
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > 128
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        for value in values
    ):
        raise ValueError(f"{field_name} contains invalid text")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be canonical")


def _require_provider_job_names(
    values: tuple[tuple[str, str], ...],
    *,
    job_ids: tuple[str, ...],
) -> None:
    if type(values) is not tuple:
        raise TypeError("provider_job_names must be an exact tuple")
    observed_job_ids: list[str] = []
    for value in values:
        if type(value) is not tuple or len(value) != 2:
            raise TypeError("provider job name entries must be exact pairs")
        job_id, job_name = value
        if (
            type(job_id) is not str
            or job_id not in job_ids
            or type(job_name) is not str
            or not job_name
            or len(job_name.encode("utf-8")) > 256
            or any(0xD800 <= ord(character) <= 0xDFFF for character in job_name)
        ):
            raise ValueError("provider job name entry is invalid")
        observed_job_ids.append(job_id)
    if tuple(sorted(set(observed_job_ids), key=utf16_sort_key)) != tuple(observed_job_ids):
        raise ValueError("provider job name entries must be canonical")


def _require_job_needs(
    values: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    job_ids: tuple[str, ...],
) -> None:
    if type(values) is not tuple:
        raise TypeError("job_needs must be an exact tuple")
    observed_job_ids: list[str] = []
    known = set(job_ids)
    for value in values:
        if type(value) is not tuple or len(value) != 2:
            raise TypeError("job dependency entries must be exact pairs")
        job_id, dependencies = value
        if type(job_id) is not str or job_id not in known:
            raise ValueError("job dependency entry has an unknown job")
        _require_canonical_text(
            dependencies,
            field_name="job dependencies",
            allow_empty=True,
        )
        if job_id in dependencies or any(dependency not in known for dependency in dependencies):
            raise ValueError("job dependency entry is invalid")
        observed_job_ids.append(job_id)
    if tuple(observed_job_ids) != job_ids:
        raise ValueError("job dependency entries must cover jobs canonically")
