"""Closure of retained provider bytes into one manifest and source binding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from ci_coordinator.kernel.canonical_json import bounded_canonical_json
from ci_coordinator.kernel.hashing import sha256_hex
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json

from ._validation import require_oid
from .errors import WorkflowAuthorityError
from .limits import (
    MANIFEST_JSON_LIMITS,
    MAX_COMMIT_RESPONSE_BYTES,
    MAX_EVIDENCE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_MANIFEST_ENTRIES,
    MAX_TREE_DEPTH,
    MAX_TREE_OBJECTS,
)
from .model import (
    GitTreeChild,
    RetainedBlobObject,
    RetainedTreeObject,
    WorkflowAuthorityManifest,
    WorkflowAuthorityRepository,
    WorkflowCommitRequest,
    WorkflowManifestEntry,
    WorkflowSourceBinding,
)

_REGULAR_MODES = frozenset({"100644", "100755"})
_WORKFLOWS_ROOT = ".github/workflows"


@dataclass(frozen=True, slots=True)
class WorkflowAuthorityEvidence:
    manifest: WorkflowAuthorityManifest
    source_binding: WorkflowSourceBinding
    commit_response: bytes
    trees: tuple[RetainedTreeObject, ...]
    blobs: tuple[RetainedBlobObject, ...]

    @classmethod
    def create(
        cls,
        *,
        repository: WorkflowAuthorityRepository,
        provider_request: WorkflowCommitRequest,
        source_commit_id: str,
        root_tree_id: str,
        commit_response: bytes,
        trees: tuple[RetainedTreeObject, ...],
        blobs: tuple[RetainedBlobObject, ...],
    ) -> Self:
        manifest, binding = _derive_artifacts(
            repository=repository,
            provider_request=provider_request,
            source_commit_id=source_commit_id,
            root_tree_id=root_tree_id,
            commit_response=commit_response,
            trees=trees,
            blobs=blobs,
        )
        return cls(manifest, binding, commit_response, trees, blobs)

    def __post_init__(self) -> None:
        if type(self.manifest) is not WorkflowAuthorityManifest:
            raise TypeError("workflow authority evidence requires an exact manifest")
        if type(self.source_binding) is not WorkflowSourceBinding:
            raise TypeError("workflow authority evidence requires an exact source binding")
        if type(self.commit_response) is not bytes or not (
            1 <= len(self.commit_response) <= MAX_COMMIT_RESPONSE_BYTES
        ):
            raise ValueError("workflow authority commit response exceeds its bound")
        _tree_index(self.trees)
        if (
            type(self.blobs) is not tuple
            or len(self.blobs) > MAX_MANIFEST_ENTRIES
            or any(type(blob) is not RetainedBlobObject for blob in self.blobs)
        ):
            raise TypeError("workflow authority blobs must be a bounded exact tuple")
        tree_paths = tuple(tree.path for tree in self.trees)
        blob_paths = tuple(blob.path for blob in self.blobs)
        if tree_paths != tuple(sorted(set(tree_paths), key=utf16_sort_key)):
            raise ValueError("workflow authority trees must have canonical unique paths")
        if blob_paths != tuple(sorted(set(blob_paths), key=utf16_sort_key)):
            raise ValueError("workflow authority blobs must have canonical unique paths")
        if (
            sum(len(tree.content) for tree in self.trees)
            + sum(len(blob.content) for blob in self.blobs)
            > MAX_EVIDENCE_BYTES
        ):
            raise ValueError("workflow authority retained evidence exceeds its aggregate bound")
        manifest, source_binding = _derive_artifacts(
            repository=self.manifest.repository,
            provider_request=self.source_binding.provider_request,
            source_commit_id=self.source_binding.source_commit_id,
            root_tree_id=self.source_binding.root_tree_id,
            commit_response=self.commit_response,
            trees=self.trees,
            blobs=self.blobs,
        )
        if manifest != self.manifest or source_binding != self.source_binding:
            raise WorkflowAuthorityError(
                "evidence_binding_mismatch",
                "workflow authority evidence does not reproduce its manifest and source binding",
            )

    @property
    def evidence_digest(self) -> str:
        return sha256_hex(
            bounded_canonical_json(
                {
                    "manifestDigest": self.manifest.manifest_digest,
                    "sourceBindingDigest": self.source_binding.binding_digest,
                    "trees": [tree.to_evidence_mapping() for tree in self.trees],
                    "blobs": [blob.to_evidence_mapping() for blob in self.blobs],
                },
                max_bytes=MAX_MANIFEST_BYTES,
                resource_limits=MANIFEST_JSON_LIMITS,
            )
        )


def _derive_artifacts(
    *,
    repository: WorkflowAuthorityRepository,
    provider_request: WorkflowCommitRequest,
    source_commit_id: str,
    root_tree_id: str,
    commit_response: bytes,
    trees: tuple[RetainedTreeObject, ...],
    blobs: tuple[RetainedBlobObject, ...],
) -> tuple[WorkflowAuthorityManifest, WorkflowSourceBinding]:
    _require_commit_response_binding(
        commit_response,
        source_commit_id=source_commit_id,
        root_tree_id=root_tree_id,
    )
    tree_index = _tree_index(trees)
    root = _require_tree(tree_index, "", root_tree_id)
    github_child = _required_tree_child(root, ".github")
    github = _require_tree(tree_index, ".github", github_child.object_id)
    workflows_child = _required_tree_child(github, "workflows")
    workflows = _require_tree(tree_index, _WORKFLOWS_ROOT, workflows_child.object_id)
    manifest = WorkflowAuthorityManifest(
        repository=repository,
        object_format="sha1",
        workflows_tree_id=workflows.object_id,
        entries=_manifest_entries(
            trees=trees,
            blobs=blobs,
            workflows_tree_id=workflows.object_id,
        ),
    )
    binding = WorkflowSourceBinding(
        repository=repository,
        provider_request=provider_request,
        source_commit_id=source_commit_id,
        commit_response_sha256=sha256_hex(commit_response),
        root_tree_id=root.object_id,
        github_tree_id=github.object_id,
        workflows_tree_id=workflows.object_id,
        manifest_digest=manifest.manifest_digest,
    )
    return manifest, binding


def _manifest_entries(
    *,
    trees: tuple[RetainedTreeObject, ...],
    blobs: tuple[RetainedBlobObject, ...],
    workflows_tree_id: str,
) -> tuple[WorkflowManifestEntry, ...]:
    tree_index = _tree_index(trees)
    blob_index = {blob.path: blob for blob in blobs}
    if len(blob_index) != len(blobs):
        raise ValueError("retained workflow blobs must have unique paths")
    workflows = _require_tree(tree_index, _WORKFLOWS_ROOT, workflows_tree_id)
    pending: list[tuple[RetainedTreeObject, int]] = [(workflows, 0)]
    visited: set[str] = set()
    entries: list[WorkflowManifestEntry] = []
    expected_blob_paths: set[str] = set()
    while pending:
        tree, depth = pending.pop()
        if tree.path in visited:
            raise WorkflowAuthorityError(
                "tree_cycle_or_duplicate",
                "workflow tree traversal revisits one repository path",
            )
        visited.add(tree.path)
        if depth > MAX_TREE_DEPTH:
            raise WorkflowAuthorityError(
                "tree_depth_exceeded",
                "workflow tree traversal exceeds its admitted depth",
            )
        for child in tree.entries:
            path = f"{tree.path}/{child.name}"
            if child.mode == "040000" and child.object_type == "tree":
                nested = _require_tree(tree_index, path, child.object_id)
                entries.append(
                    WorkflowManifestEntry(path, "040000", "tree", child.object_id, None, None, None)
                )
                pending.append((nested, depth + 1))
                continue
            if child.mode not in _REGULAR_MODES or child.object_type != "blob":
                raise WorkflowAuthorityError(
                    "unsupported_workflow_object",
                    "workflow tree contains a symlink, gitlink, special, or unknown object",
                )
            blob = blob_index.get(path)
            if blob is None or (
                blob.mode,
                blob.object_id,
                blob.declared_size,
            ) != (child.mode, child.object_id, child.declared_size):
                raise WorkflowAuthorityError(
                    "missing_workflow_blob",
                    "workflow tree entry lacks matching retained blob evidence",
                )
            expected_blob_paths.add(path)
            entries.append(
                WorkflowManifestEntry(
                    path,
                    blob.mode,
                    "blob",
                    blob.object_id,
                    blob.declared_size,
                    len(blob.content),
                    blob.sha256,
                )
            )
    expected_tree_paths = {entry.path for entry in entries if entry.object_type == "tree"}
    retained_nested_tree_paths = {
        tree.path for tree in trees if tree.path.startswith(f"{_WORKFLOWS_ROOT}/")
    }
    if expected_tree_paths != retained_nested_tree_paths or expected_blob_paths != set(blob_index):
        raise WorkflowAuthorityError(
            "incomplete_workflow_traversal",
            "retained workflow evidence has missing or unreachable descendants",
        )
    if len(entries) > MAX_MANIFEST_ENTRIES:
        raise WorkflowAuthorityError(
            "manifest_entry_limit_exceeded",
            "workflow manifest entry count exceeds its bound",
        )
    return tuple(sorted(entries, key=lambda entry: utf16_sort_key(entry.path)))


def _tree_index(trees: tuple[RetainedTreeObject, ...]) -> dict[str, RetainedTreeObject]:
    if (
        type(trees) is not tuple
        or not 3 <= len(trees) <= MAX_TREE_OBJECTS
        or any(type(tree) is not RetainedTreeObject for tree in trees)
    ):
        raise TypeError("retained tree evidence must be a bounded exact tuple")
    index = {tree.path: tree for tree in trees}
    if len(index) != len(trees):
        raise ValueError("retained tree evidence paths must be unique")
    return index


def _require_tree(
    trees: dict[str, RetainedTreeObject],
    path: str,
    object_id: str,
) -> RetainedTreeObject:
    tree = trees.get(path)
    if tree is None or tree.object_id != object_id:
        raise WorkflowAuthorityError(
            "tree_chain_mismatch",
            f"retained tree evidence does not close the path {path or '<root>'}",
        )
    return tree


def _required_tree_child(tree: RetainedTreeObject, name: str) -> GitTreeChild:
    child = next((entry for entry in tree.entries if entry.name == name), None)
    if child is None or child.object_type != "tree" or child.mode != "040000":
        raise WorkflowAuthorityError(
            "tree_chain_mismatch",
            f"retained tree evidence lacks required {name} tree",
        )
    return child


def _require_commit_response_binding(
    content: bytes,
    *,
    source_commit_id: str,
    root_tree_id: str,
) -> None:
    require_oid(source_commit_id, "source commit")
    require_oid(root_tree_id, "root tree")
    if type(content) is not bytes or not 1 <= len(content) <= MAX_COMMIT_RESPONSE_BYTES:
        raise ValueError("provider commit response must be bounded exact bytes")
    try:
        value = load_strict_json(content, max_bytes=MAX_COMMIT_RESPONSE_BYTES)
    except StrictJsonError as error:
        raise WorkflowAuthorityError(
            "invalid_commit_response",
            "provider commit response is not admitted strict JSON",
        ) from error
    if type(value) is not dict:
        raise WorkflowAuthorityError(
            "invalid_commit_response",
            "provider commit response must be an object",
        )
    tree = value.get("tree")
    if (
        value.get("sha") != source_commit_id
        or type(tree) is not dict
        or tree.get("sha") != root_tree_id
    ):
        raise WorkflowAuthorityError(
            "commit_binding_mismatch",
            "provider commit response does not bind the requested commit and root tree",
        )
