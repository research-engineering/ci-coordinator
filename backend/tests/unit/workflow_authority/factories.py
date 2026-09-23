"""Exact Git-object fixtures for workflow-authority witnesses."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import bounded_canonical_json
from ci_coordinator.workflow_authority import (
    GitTreeChild,
    RetainedBlobObject,
    RetainedTreeObject,
    WorkflowAuthorityEvidence,
    WorkflowAuthorityRepository,
    WorkflowCommitRequest,
    git_blob_oid,
    git_tree_oid,
    git_tree_sort_key,
)

REVISION = "a" * 40
WORKFLOW_PATH = ".github/workflows/ci.yml"
DEFAULT_SCOPE = RepositoryScope(11, 22)
WORKFLOW_CONTENT = (
    b"name: CI\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n"
)


def evidence(
    *,
    revision: str = REVISION,
    workflow_content: bytes = WORKFLOW_CONTENT,
    workflow_path: str = WORKFLOW_PATH,
    scope: RepositoryScope = DEFAULT_SCOPE,
    owner: str = "example-org",
    name: str = "consumer",
    default_branch: str = "master",
    unrelated_root_content: bytes | None = None,
    extra_workflows: tuple[tuple[str, bytes], ...] = (),
) -> WorkflowAuthorityEvidence:
    workflow_name = workflow_path.removeprefix(".github/workflows/")
    if "/" in workflow_name:
        raise ValueError("test evidence helper currently supports direct workflows only")
    blob_id = git_blob_oid(workflow_content)
    workflow_blob = RetainedBlobObject(
        workflow_path,
        "100644",
        blob_id,
        len(workflow_content),
        workflow_content,
    )
    blobs = [workflow_blob]
    children = [GitTreeChild(workflow_name, "100644", "blob", blob_id, len(workflow_content))]
    for path, content in extra_workflows:
        name_in_tree = path.removeprefix(".github/workflows/")
        if "/" in name_in_tree:
            raise ValueError("test evidence helper requires direct workflow paths")
        object_id = git_blob_oid(content)
        blobs.append(RetainedBlobObject(path, "100644", object_id, len(content), content))
        children.append(GitTreeChild(name_in_tree, "100644", "blob", object_id, len(content)))
    workflows = retained_tree(".github/workflows", *children)
    github = retained_tree(
        ".github",
        GitTreeChild("workflows", "040000", "tree", workflows.object_id, None),
    )
    root_children = [GitTreeChild(".github", "040000", "tree", github.object_id, None)]
    if unrelated_root_content is not None:
        root_children.append(
            GitTreeChild(
                "README.md",
                "100644",
                "blob",
                git_blob_oid(unrelated_root_content),
                len(unrelated_root_content),
            )
        )
    root = retained_tree("", *root_children)
    commit_response = bounded_canonical_json(
        {"sha": revision, "tree": {"sha": root.object_id}},
        max_bytes=1_048_576,
    )
    repository = WorkflowAuthorityRepository(scope, owner, name, default_branch)
    return WorkflowAuthorityEvidence.create(
        repository=repository,
        provider_request=WorkflowCommitRequest(
            "workflow_authority.get_commit",
            "GET",
            f"/repos/{repository.full_name}/git/commits/{revision}",
            "2026-03-10",
            (),
            True,
        ),
        source_commit_id=revision,
        root_tree_id=root.object_id,
        commit_response=commit_response,
        trees=tuple(sorted((root, github, workflows), key=lambda item: item.path)),
        blobs=tuple(sorted(blobs, key=lambda item: item.path)),
    )


def retained_tree(path: str, *children: GitTreeChild) -> RetainedTreeObject:
    entries = tuple(sorted(children, key=git_tree_sort_key))
    return RetainedTreeObject(path, git_tree_oid(entries), entries)
