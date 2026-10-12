"""Exact caller-commit component references and their bounded resolution values."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal, Self

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import git_branch_name_is_admitted, hash_object, utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_TEXT_BYTES,
    is_full_sha1,
    require_exact_tuple,
    require_identifier,
    require_sha1,
    require_sha256,
    require_text,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.graph_model import CallEdge
from ci_coordinator.workflow_discovery.report import DiscoveryReport

type ComponentKind = Literal["reusable_workflow", "action", "docker_image"]
type ComponentResolution = Literal["observed", "unresolved", "denied"]
type ComponentReadFailureReason = Literal[
    "annotated_tag_not_dereferenced",
    "installation_inventory_limit_exceeded",
    "installation_inventory_unavailable",
    "malformed_provider_response",
    "provider_binding_mismatch",
    "provider_limit_exceeded",
    "provider_unavailable",
    "rate_limited",
    "ref_not_found",
    "repository_not_found",
]
type ComponentUnresolvedReason = Literal[
    "annotated_tag_not_dereferenced",
    "callee_workflow_missing",
    "dynamic_reference",
    "installation_inventory_limit_exceeded",
    "installation_inventory_unavailable",
    "invalid_reference",
    "local_target_missing",
    "malformed_provider_response",
    "not_in_caller_installation",
    "outside_caller_installation",
    "provider_binding_mismatch",
    "provider_limit_exceeded",
    "provider_unavailable",
    "rate_limited",
    "ref_not_found",
    "registry_not_read",
    "repository_not_found",
    "resolution_deadline_exceeded",
    "resolution_limit_exceeded",
    "resolution_not_requested",
    "resolution_overloaded",
    "workspace_checkout_dependent",
]

COMPONENT_READ_FAILURE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "annotated_tag_not_dereferenced",
        "installation_inventory_limit_exceeded",
        "installation_inventory_unavailable",
        "malformed_provider_response",
        "provider_binding_mismatch",
        "provider_limit_exceeded",
        "provider_unavailable",
        "rate_limited",
        "ref_not_found",
        "repository_not_found",
    }
)
_UNRESOLVED_REASONS: Final[frozenset[str]] = COMPONENT_READ_FAILURE_REASONS | {
    "callee_workflow_missing",
    "dynamic_reference",
    "invalid_reference",
    "local_target_missing",
    "not_in_caller_installation",
    "outside_caller_installation",
    "registry_not_read",
    "resolution_deadline_exceeded",
    "resolution_limit_exceeded",
    "resolution_not_requested",
    "resolution_overloaded",
    "workspace_checkout_dependent",
}
COMPONENT_PASSPORT_NON_CLAIMS: Final = tuple(
    sorted(
        (
            "component inputs are not projected",
            "docker image tags are not resolved to digests",
            "mutable ref resolution is observed at resolvedAt and is not retained",
            "remote component contents, nested calls and actions are not parsed",
            "resolution is not omission authority",
            "reverse consumers and observed run usage are not reported",
        ),
        key=utf16_sort_key,
    )
)

# The report byte bound admits fewer than 20,000 projected steps plus at most
# 4,096 call edges, so every derivable passport fits this reference bound.
MAX_COMPONENT_REFERENCES: Final = 24_576

_OWNER = re.compile(r"^[A-Za-z0-9-]{1,39}$")
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_DOCKER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOCKER_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_MAX_REF_BYTES = 256
_LOCAL_REUSABLE_PREFIXES = ("./", "$/")


@dataclass(frozen=True, slots=True)
class ComponentReference:
    """One job-level reusable call or step action with its revision evidence.

    `repository` is `owner/name` for a remote Git component, the image name for
    a Docker image and absent for same-commit or unparseable references.
    `resolvedAt` is absent for same-commit observations because the caller
    commit, not a time-dependent provider read, binds their revision.
    """

    reference_id: str
    caller_workflow_path: str
    caller_job_id: str
    step_index: int | None
    uses: str
    kind: ComponentKind
    repository: str | None
    path: str | None
    ref: str | None
    digest: str | None
    immutable: bool
    resolution: ComponentResolution
    unresolved_reason: ComponentUnresolvedReason | None
    resolved_revision: str | None
    resolved_blob_sha: str | None
    resolved_at: datetime | None

    def __post_init__(self) -> None:
        require_identifier(self.reference_id, "component:", "component reference id")
        require_workflow_path(self.caller_workflow_path)
        require_text(self.caller_job_id, "caller job id", maximum_bytes=128)
        require_text(self.uses, "component coordinate", maximum_bytes=MAX_TEXT_BYTES)
        if self.kind not in {"reusable_workflow", "action", "docker_image"}:
            raise ValueError("component kind is not admitted")
        if self.kind == "reusable_workflow":
            if self.step_index is not None:
                raise ValueError("reusable workflow calls are job-level references")
        elif type(self.step_index) is not int or self.step_index < 0:
            raise ValueError("step components require a non-negative step index")
        for name, value in (
            ("component repository", self.repository),
            ("component path", self.path),
            ("component ref", self.ref),
            ("component digest", self.digest),
        ):
            if value is not None:
                require_text(value, name, maximum_bytes=MAX_TEXT_BYTES)
        if type(self.immutable) is not bool:
            raise TypeError("component immutability must be an exact boolean")
        self._require_resolution_shape()
        expected = _reference_id(
            self.caller_workflow_path, self.caller_job_id, self.step_index, self.uses
        )
        if self.reference_id != expected:
            raise ValueError("component reference id does not bind its caller coordinate")

    @property
    def provider_resolvable(self) -> bool:
        """Whether only an authorized provider read can observe this revision."""

        return self.kind != "docker_image" and self.repository is not None

    def declaration(self) -> ComponentReference:
        """Return the caller-commit projection before any provider read."""

        if not self.provider_resolvable:
            return self
        return replace(
            self,
            resolution="unresolved",
            unresolved_reason="resolution_not_requested",
            resolved_revision=None,
            resolved_blob_sha=None,
            resolved_at=None,
        )

    def _require_resolution_shape(self) -> None:
        if self.resolution == "observed":
            require_sha1(self.resolved_revision, "component resolved revision")
            if self.unresolved_reason is not None:
                raise ValueError("observed component cannot carry an unresolved reason")
            if (self.kind == "reusable_workflow") != (self.resolved_blob_sha is not None):
                raise ValueError("only an observed reusable workflow binds a callee blob")
            if self.resolved_blob_sha is not None:
                require_sha1(self.resolved_blob_sha, "component resolved blob")
            if self.provider_resolvable:
                if type(self.resolved_at) is not datetime or self.resolved_at.utcoffset() is None:
                    raise ValueError("provider-observed component requires an aware resolvedAt")
            elif self.resolved_at is not None or self.kind != "reusable_workflow":
                raise ValueError("same-commit observation is bound by the caller commit")
            return
        if self.resolution not in {"unresolved", "denied"}:
            raise ValueError("component resolution is not admitted")
        if (
            self.resolved_revision is not None
            or self.resolved_blob_sha is not None
            or self.resolved_at is not None
        ):
            raise ValueError("unobserved component cannot carry resolved revision evidence")
        if self.resolution == "denied":
            if self.unresolved_reason is not None or not self.provider_resolvable:
                raise ValueError("only a provider-resolvable component can be denied")
        elif self.unresolved_reason not in _UNRESOLVED_REASONS:
            raise ValueError("unresolved component requires an admitted reason")


@dataclass(frozen=True, slots=True)
class ComponentPassport:
    """Forward component references bound to one exact discovery report.

    The passport is outside the report inventory digest: a later provider read
    can observe a different revision for a mutable ref without changing the
    caller-commit evidence.
    """

    revision: str
    inventory_digest: str
    declarations_complete: bool
    references: tuple[ComponentReference, ...]
    non_claims: tuple[str, ...]

    @classmethod
    def create(
        cls,
        report: DiscoveryReport,
        references: tuple[ComponentReference, ...],
    ) -> Self:
        return cls(
            revision=report.revision,
            inventory_digest=report.inventory_digest,
            declarations_complete=component_declarations_complete(report),
            references=tuple(
                sorted(references, key=lambda item: utf16_sort_key(item.reference_id))
            ),
            non_claims=COMPONENT_PASSPORT_NON_CLAIMS,
        )

    def __post_init__(self) -> None:
        require_sha1(self.revision, "component passport revision")
        require_sha256(self.inventory_digest, "component passport inventory digest")
        if type(self.declarations_complete) is not bool:
            raise TypeError("component declaration completeness must be an exact boolean")
        require_exact_tuple(self.references, ComponentReference, "component references")
        if len(self.references) > MAX_COMPONENT_REFERENCES:
            raise ValueError("component passport exceeds its reference bound")
        reference_ids = tuple(item.reference_id for item in self.references)
        if reference_ids != tuple(sorted(set(reference_ids), key=utf16_sort_key)):
            raise ValueError("component references must have unique canonical ids")
        if self.non_claims != COMPONENT_PASSPORT_NON_CLAIMS:
            raise ValueError("component passport non-claims must use the closed catalog")

    def binds(self, report: DiscoveryReport) -> bool:
        """Whether this passport totally projects the report's declarations."""

        return (
            self.revision == report.revision
            and self.inventory_digest == report.inventory_digest
            and self.declarations_complete == component_declarations_complete(report)
            and tuple(item.declaration() for item in self.references) == declare_components(report)
        )


@dataclass(frozen=True, slots=True)
class ComponentRepositoryScope:
    """One repository found in the caller installation's provider inventory."""

    name: str
    scope: RepositoryScope

    def __post_init__(self) -> None:
        if type(self.name) is not str or _REPOSITORY_NAME.fullmatch(self.name) is None:
            raise ValueError("component repository name is not admitted")
        if type(self.scope) is not RepositoryScope:
            raise TypeError("component repository requires an exact RepositoryScope")


@dataclass(frozen=True, slots=True)
class ComponentRepositoryLocation:
    """Requested names found in one installation; `complete` means every page was read."""

    repositories: tuple[ComponentRepositoryScope, ...]
    complete: bool

    def __post_init__(self) -> None:
        require_exact_tuple(self.repositories, ComponentRepositoryScope, "located repositories")
        if type(self.complete) is not bool:
            raise TypeError("repository location completeness must be an exact boolean")
        names = tuple(item.name.lower() for item in self.repositories)
        if len(names) != len(set(names)):
            raise ValueError("located repositories must have unique case-insensitive names")


@dataclass(frozen=True, slots=True)
class ComponentWorkflowBlob:
    file_name: str
    blob_sha: str | None

    def __post_init__(self) -> None:
        require_workflow_path(f".github/workflows/{self.file_name}")
        if self.blob_sha is not None:
            require_sha1(self.blob_sha, "component workflow blob")


@dataclass(frozen=True, slots=True)
class ComponentRevisionObserved:
    """A ref resolved to one exact commit and requested callee workflow blobs."""

    commit_sha: str
    workflow_blobs: tuple[ComponentWorkflowBlob, ...]

    def __post_init__(self) -> None:
        require_sha1(self.commit_sha, "component commit")
        require_exact_tuple(self.workflow_blobs, ComponentWorkflowBlob, "component blobs")
        names = tuple(item.file_name for item in self.workflow_blobs)
        if names != tuple(sorted(set(names), key=utf16_sort_key)):
            raise ValueError("component workflow blobs must have unique canonical names")


@dataclass(frozen=True, slots=True)
class ComponentReadFailure:
    reason: ComponentReadFailureReason

    def __post_init__(self) -> None:
        if self.reason not in COMPONENT_READ_FAILURE_REASONS:
            raise ValueError("component read failure reason is not admitted")


type ComponentLocationOutcome = ComponentRepositoryLocation | ComponentReadFailure
type ComponentRevisionOutcome = ComponentRevisionObserved | ComponentReadFailure


def component_declarations_complete(report: DiscoveryReport) -> bool:
    """Every source parsed and every job-level and step `uses` was projected."""

    # A non-scalar job `uses` retains no syntax, so only its unknown reveals it.
    unprojected_calls = {
        unknown.subject_id for unknown in report.unknowns if unknown.field == "job.uses"
    }
    return report.complete and all(
        job.steps is not None and (job.uses is not None or job.subject_id not in unprojected_calls)
        for workflow in report.workflows
        for job in workflow.jobs
    )


def declare_components(report: DiscoveryReport) -> tuple[ComponentReference, ...]:
    """Project every job-level call and step action of the exact caller commit."""

    blobs = {source.path: source.blob_sha for source in report.sources}
    references = [_reusable_declaration(edge, report.revision, blobs) for edge in report.call_edges]
    for workflow in report.workflows:
        for job in workflow.jobs:
            for step in job.steps or ():
                if step.uses is None:
                    continue
                references.append(
                    _step_declaration(
                        workflow.path,
                        job.job_id,
                        step.index,
                        step.uses,
                        dynamic=step.uses_dynamic,
                    )
                )
    return tuple(sorted(references, key=lambda item: utf16_sort_key(item.reference_id)))


def _reusable_declaration(
    edge: CallEdge,
    caller_revision: str,
    blobs: dict[str, str],
) -> ComponentReference:
    caller_path, job_id, uses = edge.caller_workflow_path, edge.caller_job_id, edge.uses
    if edge.kind == "local" and edge.target_path is not None:
        blob = blobs.get(edge.target_path)
        if blob is None:
            return _unresolved(
                caller_path,
                job_id,
                None,
                uses,
                "reusable_workflow",
                "local_target_missing",
                path=edge.target_path,
                immutable=True,
            )
        return _reference(
            caller_path,
            job_id,
            None,
            uses,
            "reusable_workflow",
            repository=None,
            path=edge.target_path,
            ref=None,
            digest=None,
            immutable=True,
            resolution="observed",
            unresolved_reason=None,
            resolved_revision=caller_revision,
            resolved_blob_sha=blob,
        )
    if edge.kind == "remote" and edge.remote_ref is not None:
        coordinate = uses.rpartition("@")[0].split("/")
        repository = _git_repository(coordinate[0], coordinate[1])
        callee_path = f".github/workflows/{coordinate[4]}"
        if (
            repository is not None
            and _git_ref_is_admitted(edge.remote_ref)
            and _workflow_path_is_admitted(callee_path)
        ):
            return _unresolved(
                caller_path,
                job_id,
                None,
                uses,
                "reusable_workflow",
                "resolution_not_requested",
                repository=repository,
                path=callee_path,
                ref=edge.remote_ref,
                immutable=is_full_sha1(edge.remote_ref),
            )
    reason: ComponentUnresolvedReason = (
        "dynamic_reference" if edge.kind == "dynamic" else "invalid_reference"
    )
    return _unresolved(caller_path, job_id, None, uses, "reusable_workflow", reason)


def _step_declaration(
    caller_path: str,
    job_id: str,
    step_index: int,
    uses: str,
    *,
    dynamic: bool,
) -> ComponentReference:
    if dynamic:
        return _unresolved(caller_path, job_id, step_index, uses, "action", "dynamic_reference")
    if uses.startswith("docker://"):
        image = _docker_image(uses.removeprefix("docker://"))
        if image is None:
            return _unresolved(
                caller_path, job_id, step_index, uses, "docker_image", "invalid_reference"
            )
        name, tag, digest = image
        return _unresolved(
            caller_path,
            job_id,
            step_index,
            uses,
            "docker_image",
            "registry_not_read",
            repository=name,
            ref=tag,
            digest=digest,
            immutable=digest is not None,
        )
    if uses.startswith(_LOCAL_REUSABLE_PREFIXES[0]):
        path = uses.removeprefix("./").rstrip("/")
        if path and _relative_path_is_admitted(path) and "@" not in path:
            return _unresolved(
                caller_path,
                job_id,
                step_index,
                uses,
                "action",
                "workspace_checkout_dependent",
                path=path,
            )
        return _unresolved(caller_path, job_id, step_index, uses, "action", "invalid_reference")
    coordinate, separator, ref = uses.rpartition("@")
    parts = coordinate.split("/")
    repository = None if len(parts) < 2 else _git_repository(parts[0], parts[1])
    action_path = "/".join(parts[2:])
    if (
        separator
        and repository is not None
        and _git_ref_is_admitted(ref)
        and (not action_path or _relative_path_is_admitted(action_path))
    ):
        return _unresolved(
            caller_path,
            job_id,
            step_index,
            uses,
            "action",
            "resolution_not_requested",
            repository=repository,
            path=action_path or None,
            ref=ref,
            immutable=is_full_sha1(ref),
        )
    return _unresolved(caller_path, job_id, step_index, uses, "action", "invalid_reference")


def _unresolved(
    caller_path: str,
    job_id: str,
    step_index: int | None,
    uses: str,
    kind: ComponentKind,
    reason: ComponentUnresolvedReason,
    *,
    repository: str | None = None,
    path: str | None = None,
    ref: str | None = None,
    digest: str | None = None,
    immutable: bool = False,
) -> ComponentReference:
    return _reference(
        caller_path,
        job_id,
        step_index,
        uses,
        kind,
        repository=repository,
        path=path,
        ref=ref,
        digest=digest,
        immutable=immutable,
        resolution="unresolved",
        unresolved_reason=reason,
        resolved_revision=None,
        resolved_blob_sha=None,
    )


def _reference(
    caller_path: str,
    job_id: str,
    step_index: int | None,
    uses: str,
    kind: ComponentKind,
    *,
    repository: str | None,
    path: str | None,
    ref: str | None,
    digest: str | None,
    immutable: bool,
    resolution: ComponentResolution,
    unresolved_reason: ComponentUnresolvedReason | None,
    resolved_revision: str | None,
    resolved_blob_sha: str | None,
) -> ComponentReference:
    return ComponentReference(
        reference_id=_reference_id(caller_path, job_id, step_index, uses),
        caller_workflow_path=caller_path,
        caller_job_id=job_id,
        step_index=step_index,
        uses=uses,
        kind=kind,
        repository=repository,
        path=path,
        ref=ref,
        digest=digest,
        immutable=immutable,
        resolution=resolution,
        unresolved_reason=unresolved_reason,
        resolved_revision=resolved_revision,
        resolved_blob_sha=resolved_blob_sha,
        resolved_at=None,
    )


def _reference_id(caller_path: str, job_id: str, step_index: int | None, uses: str) -> str:
    return (
        "component:"
        + hash_object(
            {
                "callerWorkflowPath": caller_path,
                "callerJobId": job_id,
                "stepIndex": step_index,
                "uses": uses,
            }
        )[:32]
    )


def _git_repository(owner: str, name: str) -> str | None:
    if (
        _OWNER.fullmatch(owner) is None
        or _REPOSITORY_NAME.fullmatch(name) is None
        or name in {".", ".."}
    ):
        return None
    return f"{owner}/{name}"


def _git_ref_is_admitted(ref: str) -> bool:
    # Branch-name syntax rejects ASCII spaces only; the strip check also rejects
    # surrounding Unicode whitespace that the reference text bound forbids.
    return (
        bool(ref)
        and ref == ref.strip()
        and len(ref.encode("utf-8")) <= _MAX_REF_BYTES
        and (is_full_sha1(ref) or git_branch_name_is_admitted(ref))
    )


def _workflow_path_is_admitted(path: str) -> bool:
    # The parser admits any remote `.yml` file name; only a path that the callee
    # blob contract admits may become a provider read target.
    try:
        require_workflow_path(path)
    except ValueError:
        return False
    return True


def _relative_path_is_admitted(path: str) -> bool:
    return path == path.strip() and all(
        segment not in {"", ".", ".."}
        and "\\" not in segment
        and not any(ord(character) < 32 or ord(character) == 127 for character in segment)
        for segment in path.split("/")
    )


def _docker_image(image: str) -> tuple[str, str | None, str | None] | None:
    name, separator, digest = image.partition("@")
    if separator and _DOCKER_DIGEST.fullmatch(digest) is None:
        return None
    last_segment = name.rsplit("/", 1)[-1]
    tag: str | None = None
    if ":" in last_segment:
        name, _, tag = name.rpartition(":")
        if _DOCKER_TAG.fullmatch(tag) is None:
            return None
    if (
        not name
        or name != name.strip()
        or any(character.isspace() or ord(character) < 32 for character in name)
        or any(segment in {"", ".", ".."} for segment in name.split("/"))
    ):
        return None
    return name, tag, digest if separator else None
