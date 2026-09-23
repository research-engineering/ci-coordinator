from __future__ import annotations

import asyncio
import base64
import json

import httpx2 as httpx

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubWorkflowAuthorityReader,
)
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.workflow_authority import (
    GitTreeChild,
    RetainedBlobObject,
    RetainedTreeObject,
    WorkflowAuthorityEvidence,
    WorkflowAuthorityRepository,
    WorkflowAuthorityUnavailable,
    WorkflowCommitRequest,
    git_blob_oid,
    git_tree_oid,
    git_tree_sort_key,
)

from ._github_app_transport_support import _factory, _private_key, _token_response

SCOPE = RepositoryScope(11, 22)
REVISION = "a" * 40
CONTENT = b"name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"


def test_reader_binds_exact_provider_revision_to_recomputed_git_objects() -> None:
    fixture = _fixture()
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _provider_response(request, fixture)

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert isinstance(result, WorkflowAuthorityEvidence)
    assert result == fixture
    assert result.manifest.entries[0].path == ".github/workflows/ci.yml"
    assert requests == [
        "/app/installations/11/access_tokens",
        "/repositories/22",
        f"/repos/example/consumer/git/commits/{REVISION}",
        f"/repos/example/consumer/git/trees/{fixture.source_binding.root_tree_id}",
        f"/repos/example/consumer/git/trees/{fixture.source_binding.github_tree_id}",
        f"/repos/example/consumer/git/trees/{fixture.source_binding.workflows_tree_id}",
        f"/repos/example/consumer/git/blobs/{fixture.blobs[0].object_id}",
        "/repositories/22",
    ]


def test_invalid_revision_performs_no_provider_io() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid revision must not reach provider transport")

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision="A" * 40))

    assert result == WorkflowAuthorityUnavailable("invalid_revision")
    assert calls == 0


def test_provider_identity_change_during_read_fails_closed() -> None:
    fixture = _fixture()
    repository_reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal repository_reads
        if request.url.path == "/repositories/22":
            repository_reads += 1
            if repository_reads == 2:
                return _response(
                    {
                        "id": 22,
                        "owner": {"login": "example"},
                        "name": "renamed",
                        "full_name": "example/renamed",
                        "default_branch": "master",
                    }
                )
        return _provider_response(request, fixture)

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == WorkflowAuthorityUnavailable("provider_binding_mismatch")


def test_truncated_tree_cannot_become_complete_authority() -> None:
    fixture = _fixture()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/git/trees/{fixture.source_binding.workflows_tree_id}"):
            return _response(
                {
                    "sha": fixture.source_binding.workflows_tree_id,
                    "truncated": True,
                    "tree": [],
                }
            )
        return _provider_response(request, fixture)

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == WorkflowAuthorityUnavailable("source_limit_exceeded")


def test_tree_object_mismatch_is_rejected_before_blob_fetch() -> None:
    fixture = _fixture()
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith(f"/git/trees/{fixture.source_binding.workflows_tree_id}"):
            return _response(
                {
                    "sha": fixture.source_binding.workflows_tree_id,
                    "truncated": False,
                    "tree": [
                        {
                            "path": "ci.yml",
                            "mode": "100644",
                            "type": "blob",
                            "sha": "f" * 40,
                            "size": len(CONTENT),
                        }
                    ],
                }
            )
        return _provider_response(request, fixture)

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == WorkflowAuthorityUnavailable("provider_binding_mismatch")
    assert not any("/git/blobs/" in path for path in requests)


def test_commit_identity_mismatch_is_rejected_before_tree_fetch() -> None:
    fixture = _fixture()
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith(f"/git/commits/{REVISION}"):
            return _response(
                {"sha": "f" * 40, "tree": {"sha": fixture.source_binding.root_tree_id}}
            )
        return _provider_response(request, fixture)

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == WorkflowAuthorityUnavailable("provider_binding_mismatch")
    assert not any("/git/trees/" in path for path in requests)


def test_blob_content_mismatch_is_rejected_before_authority_construction() -> None:
    fixture = _fixture()
    blob = fixture.blobs[0]
    changed = b"x" * len(blob.content)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/git/blobs/{blob.object_id}"):
            return _response(
                {
                    "sha": blob.object_id,
                    "size": blob.declared_size,
                    "encoding": "base64",
                    "content": base64.b64encode(changed).decode(),
                }
            )
        return _provider_response(request, fixture)

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == WorkflowAuthorityUnavailable("malformed_provider_response")


def test_coherent_symlink_tree_is_rejected_before_blob_fetch() -> None:
    symlink_content = b"../shared/ci.yml"
    symlink_id = git_blob_oid(symlink_content)
    workflows = _tree(
        ".github/workflows",
        GitTreeChild(
            "ci.yml",
            "120000",
            "blob",
            symlink_id,
            len(symlink_content),
        ),
    )
    github = _tree(
        ".github",
        GitTreeChild("workflows", "040000", "tree", workflows.object_id, None),
    )
    root = _tree("", GitTreeChild(".github", "040000", "tree", github.object_id, None))
    trees = {item.object_id: item for item in (root, github, workflows)}
    commit = json.dumps(
        {"sha": REVISION, "tree": {"sha": root.object_id}},
        separators=(",", ":"),
    ).encode()
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path.endswith("/access_tokens"):
            return _token_response()
        if path == "/repositories/22":
            return _repository_response()
        if path.endswith(f"/git/commits/{REVISION}"):
            return _raw_response(commit)
        tree = next(
            (item for oid, item in trees.items() if path.endswith(f"/git/trees/{oid}")),
            None,
        )
        if tree is not None:
            return _tree_response(tree)
        raise AssertionError(f"unexpected GitHub request: {path}")

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == WorkflowAuthorityUnavailable("unsupported_object")
    assert not any("/git/blobs/" in path for path in requests)


async def _read(
    factory: GitHubAppTransportFactory,
    *,
    revision: str = REVISION,
) -> WorkflowAuthorityEvidence | WorkflowAuthorityUnavailable:
    try:
        return await GitHubWorkflowAuthorityReader(factory).read(scope=SCOPE, revision=revision)
    finally:
        await factory.aclose()


def _fixture() -> WorkflowAuthorityEvidence:
    blob_id = git_blob_oid(CONTENT)
    blob = RetainedBlobObject(
        ".github/workflows/ci.yml",
        "100644",
        blob_id,
        len(CONTENT),
        CONTENT,
    )
    workflows = _tree(
        ".github/workflows",
        GitTreeChild("ci.yml", "100644", "blob", blob_id, len(CONTENT)),
    )
    github = _tree(
        ".github",
        GitTreeChild("workflows", "040000", "tree", workflows.object_id, None),
    )
    root = _tree("", GitTreeChild(".github", "040000", "tree", github.object_id, None))
    commit = json.dumps(
        {"sha": REVISION, "tree": {"sha": root.object_id}},
        separators=(",", ":"),
    ).encode()
    repository = WorkflowAuthorityRepository(SCOPE, "example", "consumer", "master")
    return WorkflowAuthorityEvidence.create(
        repository=repository,
        provider_request=WorkflowCommitRequest(
            "workflow_authority.get_commit",
            "GET",
            f"/repos/{repository.full_name}/git/commits/{REVISION}",
            GITHUB_API_VERSION,
            (),
            True,
        ),
        source_commit_id=REVISION,
        root_tree_id=root.object_id,
        commit_response=commit,
        trees=(root, github, workflows),
        blobs=(blob,),
    )


def _tree(path: str, *children: GitTreeChild) -> RetainedTreeObject:
    entries = tuple(sorted(children, key=git_tree_sort_key))
    return RetainedTreeObject(path, git_tree_oid(entries), entries)


def _provider_response(
    request: httpx.Request,
    fixture: WorkflowAuthorityEvidence,
) -> httpx.Response:
    path = request.url.path
    if path.endswith("/access_tokens"):
        return _token_response()
    if path == "/repositories/22":
        return _repository_response()
    if path.endswith(f"/git/commits/{REVISION}"):
        return _raw_response(fixture.commit_response)
    tree = next(
        (item for item in fixture.trees if path.endswith(f"/git/trees/{item.object_id}")),
        None,
    )
    if tree is not None:
        return _tree_response(tree)
    blob = fixture.blobs[0]
    if path.endswith(f"/git/blobs/{blob.object_id}"):
        return _response(
            {
                "sha": blob.object_id,
                "size": blob.declared_size,
                "encoding": "base64",
                "content": base64.b64encode(blob.content).decode(),
            }
        )
    raise AssertionError(f"unexpected GitHub request: {path}")


def _response(value: object) -> httpx.Response:
    return _raw_response(json.dumps(value, separators=(",", ":")).encode())


def _repository_response() -> httpx.Response:
    return _response(
        {
            "id": 22,
            "owner": {"login": "example"},
            "name": "consumer",
            "full_name": "example/consumer",
            "default_branch": "master",
        }
    )


def _tree_response(tree: RetainedTreeObject) -> httpx.Response:
    return _response(
        {
            "sha": tree.object_id,
            "truncated": False,
            "tree": [
                {
                    "path": child.name,
                    "mode": child.mode,
                    "type": child.object_type,
                    "sha": child.object_id,
                    "size": child.declared_size,
                }
                for child in tree.entries
            ],
        }
    )


def _raw_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "x-github-api-version-selected": GITHUB_API_VERSION,
        },
        content=body,
    )
