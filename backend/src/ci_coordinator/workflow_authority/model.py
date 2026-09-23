"""Stable workflow manifest and exact source-binding evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import quote

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.canonical_json import bounded_canonical_json
from ci_coordinator.kernel.git_reference import git_branch_name_is_admitted
from ci_coordinator.kernel.hashing import sha256_hex
from ci_coordinator.kernel.ordering import utf16_sort_key

from ._validation import (
    require_component,
    require_oid,
    require_path,
    require_sha256,
    require_text,
)
from .errors import WorkflowAuthorityError
from .git_objects import git_blob_oid, git_tree_content, git_tree_oid, git_tree_sort_key
from .limits import (
    MANIFEST_JSON_LIMITS,
    MAX_BLOB_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_MANIFEST_ENTRIES,
    MAX_TREE_CONTENT_BYTES,
    MAX_TREE_ENTRIES,
)

type GitObjectFormat = Literal["sha1"]
type GitObjectType = Literal["blob", "commit", "tree"]

WORKFLOW_AUTHORITY_MANIFEST_SCHEMA: Final = "ci-coordinator.workflow-authority-manifest/v1"
WORKFLOW_SOURCE_BINDING_SCHEMA: Final = "ci-coordinator.workflow-source-binding/v1"

_REGULAR_MODES = frozenset({"100644", "100755"})
_COHERENT_MODES: Final = {
    "040000": "tree",
    "100644": "blob",
    "100755": "blob",
    "120000": "blob",
    "160000": "commit",
}
_WORKFLOWS_ROOT = ".github/workflows"


@dataclass(frozen=True, slots=True)
class WorkflowAuthorityRepository:
    scope: RepositoryScope
    owner: str
    name: str
    default_branch: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("workflow authority requires an exact repository scope")
        for value, label, maximum in (
            (self.owner, "repository owner", 512),
            (self.name, "repository name", 512),
            (self.default_branch, "default branch", 1_024),
        ):
            require_text(value, label, maximum_bytes=maximum)
        if any(
            value in {".", ".."} or "/" in value or "\0" in value
            for value in (self.owner, self.name)
        ):
            raise ValueError("repository owner and name must be safe path components")
        if not git_branch_name_is_admitted(self.default_branch):
            raise ValueError("repository default branch must be a canonical Git branch name")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def to_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "owner": self.owner,
            "name": self.name,
            "defaultBranch": self.default_branch,
        }


@dataclass(frozen=True, slots=True)
class WorkflowCommitRequest:
    operation: Literal["workflow_authority.get_commit"]
    method: Literal["GET"]
    path: str
    api_version: str
    query: tuple[tuple[str, str], ...]
    body_absent: bool

    def __post_init__(self) -> None:
        if self.operation != "workflow_authority.get_commit" or self.method != "GET":
            raise ValueError("workflow commit request operation and method are not admitted")
        require_text(self.path, "provider request path", maximum_bytes=2_048)
        require_text(self.api_version, "provider API version", maximum_bytes=64)
        if type(self.query) is not tuple or self.query:
            raise ValueError("workflow commit request query must be an exact empty tuple")
        if type(self.body_absent) is not bool or not self.body_absent:
            raise ValueError("workflow commit request body must be absent")

    def to_mapping(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "method": self.method,
            "path": self.path,
            "apiVersion": self.api_version,
            "query": [list(item) for item in self.query],
            "bodyAbsent": self.body_absent,
        }


@dataclass(frozen=True, slots=True)
class GitTreeChild:
    name: str
    mode: str
    object_type: GitObjectType
    object_id: str
    declared_size: int | None

    def __post_init__(self) -> None:
        require_component(self.name)
        if _COHERENT_MODES.get(self.mode) != self.object_type:
            raise ValueError("Git tree child mode and object type are incoherent")
        require_oid(self.object_id, "Git tree child")
        if self.object_type == "blob":
            if type(self.declared_size) is not int or self.declared_size < 0:
                raise ValueError("Git blob child requires a non-negative declared size")
        elif self.declared_size is not None:
            raise ValueError("non-blob Git child cannot carry a declared size")

    def to_mapping(self) -> dict[str, object]:
        return {
            "name": self.name,
            "mode": self.mode,
            "objectType": self.object_type,
            "objectId": self.object_id,
            "declaredSize": self.declared_size,
        }


@dataclass(frozen=True, slots=True)
class RetainedTreeObject:
    path: str
    object_id: str
    entries: tuple[GitTreeChild, ...]

    def __post_init__(self) -> None:
        require_path(self.path, allow_root=True)
        require_oid(self.object_id, "Git tree")
        if (
            type(self.entries) is not tuple
            or len(self.entries) > MAX_TREE_ENTRIES
            or any(type(entry) is not GitTreeChild for entry in self.entries)
        ):
            raise TypeError("retained Git tree entries must be a bounded exact tuple")
        names = tuple(entry.name for entry in self.entries)
        if len(names) != len(set(names)):
            raise ValueError("retained Git tree entries must have unique names")
        if tuple(sorted(self.entries, key=lambda entry: git_tree_sort_key(entry))) != self.entries:
            raise ValueError("retained Git tree entries must use canonical Git order")
        if len(self.content) > MAX_TREE_CONTENT_BYTES:
            raise ValueError("retained Git tree object exceeds its byte bound")
        if git_tree_oid(self.entries) != self.object_id:
            raise WorkflowAuthorityError(
                "tree_object_mismatch",
                "retained Git tree object id does not match its exact children",
            )

    @property
    def content(self) -> bytes:
        return git_tree_content(self.entries)

    def to_evidence_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "objectId": self.object_id,
            "contentSha256": sha256_hex(self.content),
            "entries": [entry.to_mapping() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class RetainedBlobObject:
    path: str
    mode: Literal["100644", "100755"]
    object_id: str
    declared_size: int
    content: bytes

    def __post_init__(self) -> None:
        require_path(self.path)
        if self.mode not in _REGULAR_MODES:
            raise ValueError("retained workflow blob must use a regular-file mode")
        require_oid(self.object_id, "Git blob")
        if type(self.content) is not bytes or len(self.content) > MAX_BLOB_BYTES:
            raise ValueError("retained workflow blob must be bounded exact bytes")
        if type(self.declared_size) is not int or self.declared_size != len(self.content):
            raise WorkflowAuthorityError(
                "blob_size_mismatch",
                "retained workflow blob size contradicts provider metadata",
            )
        if git_blob_oid(self.content) != self.object_id:
            raise WorkflowAuthorityError(
                "blob_object_mismatch",
                "retained workflow blob id does not bind its exact bytes",
            )

    @property
    def sha256(self) -> str:
        return sha256_hex(self.content)

    def to_evidence_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "mode": self.mode,
            "objectId": self.object_id,
            "declaredSize": self.declared_size,
            "observedSize": len(self.content),
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class WorkflowManifestEntry:
    path: str
    mode: Literal["040000", "100644", "100755"]
    object_type: Literal["blob", "tree"]
    object_id: str
    declared_size: int | None
    observed_size: int | None
    blob_sha256: str | None

    def __post_init__(self) -> None:
        require_path(self.path)
        if not self.path.startswith(f"{_WORKFLOWS_ROOT}/"):
            raise ValueError("workflow manifest entries must be below .github/workflows")
        require_oid(self.object_id, "workflow manifest entry")
        if self.object_type == "tree":
            if self.mode != "040000" or any(
                value is not None
                for value in (self.declared_size, self.observed_size, self.blob_sha256)
            ):
                raise ValueError("workflow manifest tree entry has invalid nullable fields")
            return
        if self.object_type != "blob" or self.mode not in _REGULAR_MODES:
            raise ValueError("workflow manifest object type or mode is not admitted")
        if (
            type(self.declared_size) is not int
            or self.declared_size < 0
            or type(self.observed_size) is not int
            or self.observed_size != self.declared_size
            or type(self.blob_sha256) is not str
            or len(self.blob_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.blob_sha256)
        ):
            raise ValueError("workflow manifest blob entry has invalid evidence fields")

    def to_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "mode": self.mode,
            "objectType": self.object_type,
            "objectId": self.object_id,
            "declaredSize": self.declared_size,
            "observedSize": self.observed_size,
            "blobSha256": self.blob_sha256,
        }


@dataclass(frozen=True, slots=True)
class WorkflowAuthorityManifest:
    repository: WorkflowAuthorityRepository
    object_format: GitObjectFormat
    workflows_tree_id: str
    entries: tuple[WorkflowManifestEntry, ...]

    def __post_init__(self) -> None:
        if type(self.repository) is not WorkflowAuthorityRepository:
            raise TypeError("workflow manifest requires an exact repository")
        if self.object_format != "sha1":
            raise ValueError("workflow manifest object format is not admitted")
        require_oid(self.workflows_tree_id, "workflows tree")
        if (
            type(self.entries) is not tuple
            or len(self.entries) > MAX_MANIFEST_ENTRIES
            or any(type(entry) is not WorkflowManifestEntry for entry in self.entries)
        ):
            raise TypeError("workflow manifest entries must be a bounded exact tuple")
        paths = tuple(entry.path for entry in self.entries)
        if paths != tuple(sorted(set(paths), key=utf16_sort_key)):
            raise ValueError("workflow manifest paths must be canonical and unique")
        _require_manifest_parent_closure(self.entries)
        _ = self.canonical_bytes

    @property
    def canonical_bytes(self) -> bytes:
        return bounded_canonical_json(
            self.to_mapping(),
            max_bytes=MAX_MANIFEST_BYTES,
            resource_limits=MANIFEST_JSON_LIMITS,
        )

    @property
    def manifest_digest(self) -> str:
        return sha256_hex(
            WORKFLOW_AUTHORITY_MANIFEST_SCHEMA.encode("ascii") + b"\0" + self.canonical_bytes
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": WORKFLOW_AUTHORITY_MANIFEST_SCHEMA,
            "repository": self.repository.to_mapping(),
            "objectFormat": self.object_format,
            "workflowsTreeId": self.workflows_tree_id,
            "entries": [entry.to_mapping() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class WorkflowSourceBinding:
    repository: WorkflowAuthorityRepository
    provider_request: WorkflowCommitRequest
    source_commit_id: str
    commit_response_sha256: str
    root_tree_id: str
    github_tree_id: str
    workflows_tree_id: str
    manifest_digest: str

    def __post_init__(self) -> None:
        if type(self.repository) is not WorkflowAuthorityRepository:
            raise TypeError("workflow source binding requires an exact repository")
        if type(self.provider_request) is not WorkflowCommitRequest:
            raise TypeError("workflow source binding requires an exact provider request")
        for value, label in (
            (self.source_commit_id, "source commit"),
            (self.root_tree_id, "root tree"),
            (self.github_tree_id, ".github tree"),
            (self.workflows_tree_id, "workflows tree"),
        ):
            require_oid(value, label)
        for value, label in (
            (self.commit_response_sha256, "commit response"),
            (self.manifest_digest, "workflow manifest"),
        ):
            require_sha256(value, label)
        repository_path = (
            f"/repos/{quote(self.repository.owner, safe='')}/{quote(self.repository.name, safe='')}"
        )
        if self.provider_request.path != (
            f"{repository_path}/git/commits/{quote(self.source_commit_id, safe='')}"
        ):
            raise ValueError("workflow provider request crosses repository or commit identity")
        _ = self.canonical_bytes

    @property
    def api_version(self) -> str:
        return self.provider_request.api_version

    @property
    def canonical_bytes(self) -> bytes:
        return bounded_canonical_json(
            self.to_mapping(),
            max_bytes=32_768,
            resource_limits=MANIFEST_JSON_LIMITS,
        )

    @property
    def binding_digest(self) -> str:
        return sha256_hex(
            WORKFLOW_SOURCE_BINDING_SCHEMA.encode("ascii") + b"\0" + self.canonical_bytes
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": WORKFLOW_SOURCE_BINDING_SCHEMA,
            "repository": self.repository.to_mapping(),
            "providerRequest": self.provider_request.to_mapping(),
            "sourceCommitId": self.source_commit_id,
            "commitResponseSha256": self.commit_response_sha256,
            "rootTreeId": self.root_tree_id,
            "ancestorTreeIds": [self.github_tree_id, self.workflows_tree_id],
            "manifestDigest": self.manifest_digest,
        }


def _require_manifest_parent_closure(entries: tuple[WorkflowManifestEntry, ...]) -> None:
    tree_paths = {entry.path for entry in entries if entry.object_type == "tree"}
    for entry in entries:
        relative = entry.path.removeprefix(f"{_WORKFLOWS_ROOT}/")
        parent, separator, _ = relative.rpartition("/")
        if separator and f"{_WORKFLOWS_ROOT}/{parent}" not in tree_paths:
            raise ValueError("workflow manifest entry lacks its direct parent tree row")
