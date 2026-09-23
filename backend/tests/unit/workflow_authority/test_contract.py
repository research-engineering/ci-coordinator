from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.kernel import bounded_canonical_json
from ci_coordinator.workflow_authority import (
    GitTreeChild,
    RetainedBlobObject,
    WorkflowAuthorityError,
    WorkflowAuthorityEvidence,
    WorkflowAuthorityRepository,
    WorkflowManifestEntry,
    decode_manifest,
    decode_source_binding,
    encode_manifest,
    encode_source_binding,
    git_blob_oid,
    git_tree_oid,
)

from .factories import DEFAULT_SCOPE, WORKFLOW_CONTENT, WORKFLOW_PATH, evidence, retained_tree


def test_git_object_construction_matches_independent_git_golden_vectors() -> None:
    assert git_blob_oid(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
    assert git_tree_oid(()) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    child = GitTreeChild(
        "ci.yml",
        "100644",
        "blob",
        "ce013625030ba8dba906f756967f9e9ca394464a",
        6,
    )
    assert git_tree_oid((child,)) == "c4430fe9bc2da13d3deedd4f20ad99ce407f93bc"


def test_manifest_and_source_binding_codecs_are_exact_round_trips() -> None:
    value = evidence()

    assert decode_manifest(encode_manifest(value.manifest)) == value.manifest
    assert decode_source_binding(encode_source_binding(value.source_binding)) == (
        value.source_binding
    )


@pytest.mark.parametrize("suffix", [b" ", b"\n", b"\t"], ids=["space", "newline", "tab"])
def test_codecs_reject_noncanonical_json(suffix: bytes) -> None:
    value = evidence()

    with pytest.raises(WorkflowAuthorityError, match="canonical"):
        decode_manifest(value.manifest.canonical_bytes + suffix)


def test_non_ci_commit_changes_only_the_source_binding() -> None:
    first = evidence(revision="a" * 40, unrelated_root_content=b"first\n")
    second = evidence(revision="b" * 40, unrelated_root_content=b"second\n")

    assert first.manifest.manifest_digest == second.manifest.manifest_digest
    assert first.source_binding.binding_digest != second.source_binding.binding_digest
    assert first.source_binding.root_tree_id != second.source_binding.root_tree_id


@pytest.mark.parametrize(
    ("declared_size", "content", "object_id", "expected_code"),
    (
        (
            len(WORKFLOW_CONTENT) + 1,
            WORKFLOW_CONTENT,
            git_blob_oid(WORKFLOW_CONTENT),
            "blob_size_mismatch",
        ),
        (
            len(WORKFLOW_CONTENT),
            WORKFLOW_CONTENT + b"x",
            git_blob_oid(WORKFLOW_CONTENT),
            "blob_size_mismatch",
        ),
        (len(WORKFLOW_CONTENT), WORKFLOW_CONTENT, "f" * 40, "blob_object_mismatch"),
    ),
    ids=("provider-size", "observed-size", "object-id"),
)
def test_blob_mutations_fail_closed(
    declared_size: int,
    content: bytes,
    object_id: str,
    expected_code: str,
) -> None:
    with pytest.raises(WorkflowAuthorityError) as raised:
        RetainedBlobObject(WORKFLOW_PATH, "100644", object_id, declared_size, content)

    assert raised.value.code == expected_code


def test_tree_object_mutation_fails_closed() -> None:
    valid = evidence().trees[-1]
    altered_child = replace(valid.entries[0], object_id="f" * 40)

    with pytest.raises(WorkflowAuthorityError, match="tree object id"):
        replace(valid, entries=(altered_child,))


def test_nested_workflow_tree_requires_and_retains_every_descendant() -> None:
    base = evidence()
    content = b"support evidence\n"
    blob = RetainedBlobObject(
        ".github/workflows/support/notes.txt",
        "100644",
        git_blob_oid(content),
        len(content),
        content,
    )
    nested = retained_tree(
        ".github/workflows/support",
        GitTreeChild("notes.txt", "100644", "blob", blob.object_id, len(content)),
    )
    workflows = retained_tree(
        ".github/workflows",
        GitTreeChild("support", "040000", "tree", nested.object_id, None),
    )
    github = retained_tree(
        ".github",
        GitTreeChild("workflows", "040000", "tree", workflows.object_id, None),
    )
    root = retained_tree(
        "",
        GitTreeChild(".github", "040000", "tree", github.object_id, None),
    )
    commit = bounded_canonical_json(
        {"sha": "a" * 40, "tree": {"sha": root.object_id}},
        max_bytes=1_048_576,
    )

    result = WorkflowAuthorityEvidence.create(
        repository=base.manifest.repository,
        provider_request=base.source_binding.provider_request,
        source_commit_id="a" * 40,
        root_tree_id=root.object_id,
        commit_response=commit,
        trees=tuple(sorted((root, github, workflows, nested), key=lambda item: item.path)),
        blobs=(blob,),
    )

    assert tuple(entry.path for entry in result.manifest.entries) == (
        ".github/workflows/support",
        ".github/workflows/support/notes.txt",
    )

    with pytest.raises(WorkflowAuthorityError, match="close the path"):
        replace(result, trees=tuple(tree for tree in result.trees if tree != nested))


@pytest.mark.parametrize(
    ("mode", "object_type"),
    (("120000", "blob"), ("160000", "commit")),
    ids=("symlink", "gitlink"),
)
def test_special_workflow_objects_are_rejected(
    mode: str,
    object_type: str,
) -> None:
    base = evidence()
    special = GitTreeChild(
        "unsupported.yml",
        mode,
        object_type,  # type: ignore[arg-type]
        "f" * 40,
        1 if object_type == "blob" else None,
    )
    workflows = retained_tree(".github/workflows", *base.trees[-1].entries, special)
    github = retained_tree(
        ".github",
        GitTreeChild("workflows", "040000", "tree", workflows.object_id, None),
    )
    root = retained_tree(
        "",
        GitTreeChild(".github", "040000", "tree", github.object_id, None),
    )
    commit = bounded_canonical_json(
        {"sha": "a" * 40, "tree": {"sha": root.object_id}},
        max_bytes=1_048_576,
    )

    with pytest.raises(WorkflowAuthorityError, match="symlink, gitlink"):
        WorkflowAuthorityEvidence.create(
            repository=base.manifest.repository,
            provider_request=base.source_binding.provider_request,
            source_commit_id="a" * 40,
            root_tree_id=root.object_id,
            commit_response=commit,
            trees=(root, github, workflows),
            blobs=base.blobs,
        )


def test_manifest_tree_nullable_fields_are_closed() -> None:
    with pytest.raises(ValueError, match="nullable"):
        WorkflowManifestEntry(
            ".github/workflows/nested",
            "040000",
            "tree",
            "f" * 40,
            1,
            None,
            None,
        )


def test_manifest_blob_size_requires_an_exact_integer() -> None:
    valid = evidence().manifest.entries[0]

    with pytest.raises(ValueError, match="evidence fields"):
        replace(valid, declared_size=1, observed_size=True)


def test_source_binding_rejects_reconstructed_cross_repository_request() -> None:
    value = evidence()
    request = replace(
        value.source_binding.provider_request,
        path=f"/repos/other/consumer/git/commits/{value.source_binding.source_commit_id}",
    )

    with pytest.raises(ValueError, match="crosses repository or commit"):
        replace(value.source_binding, provider_request=request)


@pytest.mark.parametrize(
    ("owner", "name"),
    ((".", "consumer"), ("example", ".."), ("example/name", "consumer"), ("example", "bad/name")),
    ids=("dot-owner", "dotdot-name", "owner-slash", "name-slash"),
)
def test_repository_identity_rejects_unsafe_components(owner: str, name: str) -> None:
    with pytest.raises(ValueError, match="safe path components"):
        WorkflowAuthorityRepository(DEFAULT_SCOPE, owner, name, "master")


@pytest.mark.parametrize(
    "default_branch",
    (".", "..", "bad..branch", "-leading", "trailing.lock"),
)
def test_repository_identity_rejects_invalid_default_branch(default_branch: str) -> None:
    with pytest.raises(ValueError, match="canonical Git branch"):
        WorkflowAuthorityRepository(DEFAULT_SCOPE, "example", "consumer", default_branch)
