"""Exact-commit, content-addressed workflow source contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Literal, Self

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._validation import (
    MAX_WORKFLOW_AGGREGATE_BYTES,
    MAX_WORKFLOW_FILE_BYTES,
    MAX_WORKFLOW_FILES,
    require_exact_tuple,
    require_sha1,
    require_text,
    require_workflow_path,
)
from ci_coordinator.workflow_discovery.adoption_target import AdoptionTargetProjection

type WorkflowSourceFailureReason = Literal[
    "malformed_provider_response",
    "not_found",
    "provider_binding_mismatch",
    "rate_limited",
    "unsupported_object",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    scope: RepositoryScope
    owner: str
    name: str
    default_branch: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("repository identity requires an exact RepositoryScope")
        require_text(self.owner, "repository owner", maximum_bytes=512)
        require_text(self.name, "repository name", maximum_bytes=512)
        require_text(self.default_branch, "default branch", maximum_bytes=1_024)

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "owner": self.owner,
            "name": self.name,
            "defaultBranch": self.default_branch,
        }


@dataclass(frozen=True, slots=True)
class WorkflowSourceIdentity:
    path: str
    blob_sha: str
    size: int

    def __post_init__(self) -> None:
        require_workflow_path(self.path)
        require_sha1(self.blob_sha, "workflow blob SHA")
        if type(self.size) is not int or not 0 <= self.size <= MAX_WORKFLOW_FILE_BYTES:
            raise ValueError("workflow source size is outside the admitted bound")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"path": self.path, "blobSha": self.blob_sha, "size": self.size}


@dataclass(frozen=True, slots=True)
class WorkflowSource:
    path: str
    blob_sha: str
    declared_size: int
    content: bytes

    def __post_init__(self) -> None:
        require_workflow_path(self.path)
        require_sha1(self.blob_sha, "workflow blob SHA")
        if type(self.content) is not bytes or len(self.content) > MAX_WORKFLOW_FILE_BYTES:
            raise ValueError("workflow content must be bounded exact bytes")
        if type(self.declared_size) is not int or self.declared_size != len(self.content):
            raise ValueError("workflow declared size must equal decoded content size")
        if self.blob_sha != git_blob_sha1(self.content):
            raise ValueError("workflow blob SHA must bind the exact Git blob content")

    @property
    def identity(self) -> WorkflowSourceIdentity:
        return WorkflowSourceIdentity(self.path, self.blob_sha, self.declared_size)


@dataclass(frozen=True, slots=True)
class WorkflowSourceFailure:
    path: str
    blob_sha: str
    declared_size: int
    reason: WorkflowSourceFailureReason

    def __post_init__(self) -> None:
        WorkflowSourceIdentity(self.path, self.blob_sha, self.declared_size)
        if self.reason not in {
            "malformed_provider_response",
            "not_found",
            "provider_binding_mismatch",
            "rate_limited",
            "unsupported_object",
            "unavailable",
        }:
            raise ValueError("workflow source failure reason is not admitted")

    @property
    def identity(self) -> WorkflowSourceIdentity:
        return WorkflowSourceIdentity(self.path, self.blob_sha, self.declared_size)


def git_blob_sha1(content: bytes) -> str:
    if type(content) is not bytes:
        raise TypeError("Git blob identity requires exact bytes")
    header = f"blob {len(content)}\0".encode("ascii")
    return sha1(header + content, usedforsecurity=False).hexdigest()


@dataclass(frozen=True, slots=True)
class RepositoryWorkflowSnapshot:
    repository: RepositoryIdentity
    revision: str
    sources: tuple[WorkflowSource, ...]
    failures: tuple[WorkflowSourceFailure, ...]
    target_projection: AdoptionTargetProjection

    @classmethod
    def create(
        cls,
        *,
        repository: RepositoryIdentity,
        revision: str,
        sources: tuple[WorkflowSource, ...],
        failures: tuple[WorkflowSourceFailure, ...] = (),
        target_projection: AdoptionTargetProjection | None = None,
    ) -> Self:
        return cls(
            repository=repository,
            revision=revision,
            sources=tuple(sorted(sources, key=lambda item: utf16_sort_key(item.path))),
            failures=tuple(sorted(failures, key=lambda item: utf16_sort_key(item.path))),
            target_projection=(
                AdoptionTargetProjection.absent()
                if target_projection is None
                else target_projection
            ),
        )

    def __post_init__(self) -> None:
        if type(self.repository) is not RepositoryIdentity:
            raise TypeError("workflow snapshot requires an exact repository identity")
        require_sha1(self.revision, "snapshot revision")
        require_exact_tuple(self.sources, WorkflowSource, "workflow sources")
        require_exact_tuple(self.failures, WorkflowSourceFailure, "workflow source failures")
        if type(self.target_projection) is not AdoptionTargetProjection:
            raise TypeError("workflow snapshot target projection must be exact")
        if len(self.sources) + len(self.failures) > MAX_WORKFLOW_FILES:
            raise ValueError("workflow snapshot exceeds its file-count bound")
        source_paths = tuple(source.path for source in self.sources)
        failure_paths = tuple(source.path for source in self.failures)
        paths = (*source_paths, *failure_paths)
        if (
            source_paths != tuple(sorted(set(source_paths), key=utf16_sort_key))
            or failure_paths != tuple(sorted(set(failure_paths), key=utf16_sort_key))
            or len(set(paths)) != len(paths)
        ):
            raise ValueError("workflow sources must have unique canonical paths")
        aggregate_size = sum(source.declared_size for source in self.sources) + sum(
            failure.declared_size for failure in self.failures
        )
        if aggregate_size > MAX_WORKFLOW_AGGREGATE_BYTES:
            raise ValueError("workflow snapshot exceeds its aggregate byte bound")

    @property
    def source_identities(self) -> tuple[WorkflowSourceIdentity, ...]:
        identities = tuple(source.identity for source in self.sources) + tuple(
            failure.identity for failure in self.failures
        )
        return tuple(sorted(identities, key=lambda item: utf16_sort_key(item.path)))
