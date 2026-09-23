from __future__ import annotations

import asyncio
import base64
import json

import httpx2 as httpx
import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration import TARGET_CONTROL_FILE_PATHS
from ci_coordinator.integrations.github import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.workflow_discovery import GitHubWorkflowSnapshotReader
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery.outcomes import (
    SnapshotReadOutcome,
    WorkflowDiscoveryUnavailable,
)
from ci_coordinator.workflow_discovery.source import (
    RepositoryWorkflowSnapshot,
    git_blob_sha1,
)

from ._github_app_transport_support import _factory, _private_key, _token_response

COMMIT_SHA = "a" * 40
ROOT_TREE_SHA = "b" * 40
GITHUB_TREE_SHA = "c" * 40
WORKFLOWS_TREE_SHA = "d" * 40
TARGET_TREE_SHA = "e" * 40
CONTENT = (
    b"name: CI\non: push\njobs:\n  test:\n"
    b"    name: Full CI\n    runs-on: ubuntu-latest\n    steps: []\n"
)
BLOB_SHA = git_blob_sha1(CONTENT)
REGISTRY_CONTENT = json.dumps(
    {
        "schemaVersion": "dynamic-ci-target-execution-registry/v1",
        "generator": {"id": "target-workflow", "version": "1"},
        "adapterFiles": [
            {"path": path, "sha256": "0" * 64}
            for path in sorted(
                (*TARGET_CONTROL_FILE_PATHS, ".github/workflows/ci.yml"),
                key=utf16_sort_key,
            )
        ],
        "workflows": [
            {
                "workflowPath": ".github/workflows/ci.yml",
                "executionKind": "native-job-set",
                "executionJobs": [{"jobId": "test", "needs": ["plan"]}],
                "planRequestJobId": "plan-request",
                "planRequestWorkflowRef": (
                    "example-org/ci-coordinator/"
                    ".github/workflows/trusted-plan-request.yml@" + "1" * 40
                ),
                "planJobId": "plan",
                "fallbackJobId": None,
                "gateJobId": "gate",
                "gateSignalName": "Pull Request Gate",
                "requiredJobIds": [],
            }
        ],
        "profiles": [
            {
                "profileId": "python",
                "workflowPath": ".github/workflows/ci.yml",
                "jobId": "test",
                "executionKind": "native-job-set",
                "runnerProfileId": "ubuntu",
                "permissionProfileId": "contents-read",
                "credentialProfileId": "none",
                "fixtureProfileId": "unit",
                "serviceProfileIds": [],
                "capacityClassId": "hosted",
            }
        ],
    },
    separators=(",", ":"),
).encode()


def test_reader_binds_numeric_repository_to_one_commit_tree_blob_chain() -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _discovery_response(request)

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory))

    assert isinstance(result, RepositoryWorkflowSnapshot)
    assert result.revision == COMMIT_SHA
    assert result.repository.scope == RepositoryScope(77, 501)
    assert result.sources[0].content == CONTENT
    assert result.failures == ()
    assert requests == [
        "/app/installations/77/access_tokens",
        "/repositories/501",
        "/repos/example/repo/git/ref/heads/master",
        f"/repos/example/repo/git/commits/{COMMIT_SHA}",
        f"/repos/example/repo/git/trees/{ROOT_TREE_SHA}",
        f"/repos/example/repo/git/trees/{GITHUB_TREE_SHA}",
        f"/repos/example/repo/git/trees/{WORKFLOWS_TREE_SHA}",
        f"/repos/example/repo/git/blobs/{BLOB_SHA}",
        "/repositories/501",
    ]


def test_invalid_revision_performs_no_provider_io() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid revision must not reach provider transport")

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision="a" * 41))

    assert result == WorkflowDiscoveryUnavailable("invalid_revision")
    assert calls == 0


def test_shared_snapshot_admission_rejects_overlap_before_provider_io() -> None:
    async def scenario() -> tuple[SnapshotReadOutcome, SnapshotReadOutcome, int]:
        entered = asyncio.Event()
        release = asyncio.Event()
        provider_calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal provider_calls
            provider_calls += 1
            if request.url.path.endswith("/access_tokens") and not entered.is_set():
                entered.set()
                await release.wait()
            return _discovery_response(request)

        factory = _factory(_private_key(), httpx.MockTransport(handler))
        reader = GitHubWorkflowSnapshotReader(factory)
        first_task = asyncio.create_task(
            reader.read(scope=RepositoryScope(77, 501), revision=COMMIT_SHA)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            second = await reader.read(
                scope=RepositoryScope(77, 501),
                revision=COMMIT_SHA,
            )
            calls_before_release = provider_calls
            release.set()
            first = await first_task
            return first, second, calls_before_release
        finally:
            release.set()
            if not first_task.done():
                first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)
            await factory.aclose()

    first, second, provider_calls = asyncio.run(scenario())

    assert isinstance(first, RepositoryWorkflowSnapshot)
    assert second == WorkflowDiscoveryUnavailable("overloaded")
    assert provider_calls == 1


def test_one_blob_failure_remains_an_explicit_partial_source() -> None:
    second_content = CONTENT.replace(b"Full CI", b"Other CI")
    second_sha = git_blob_sha1(second_content)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/git/blobs/{second_sha}"):
            return _response(b'{"message":"not found"}', status=404)
        return _discovery_response(
            request,
            workflow_entries=(
                _blob_entry("ci.yml", BLOB_SHA, len(CONTENT)),
                _blob_entry("other.yaml", second_sha, len(second_content)),
            ),
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision=COMMIT_SHA))

    assert isinstance(result, RepositoryWorkflowSnapshot)
    assert [source.path for source in result.sources] == [".github/workflows/ci.yml"]
    assert [(failure.path, failure.reason) for failure in result.failures] == [
        (".github/workflows/other.yaml", "not_found")
    ]


def test_non_files_are_ignored_and_unsupported_blob_modes_are_explicit() -> None:
    symlink_content = b"../shared/ci.yml"
    symlink_sha = git_blob_sha1(symlink_content)

    async def handler(request: httpx.Request) -> httpx.Response:
        return _discovery_response(
            request,
            workflow_entries=(
                _blob_entry("ci.yml", BLOB_SHA, len(CONTENT)),
                _tree_entry("directory.yml", "e" * 40),
                _blob_entry("linked.yaml", symlink_sha, len(symlink_content), mode="120000"),
            ),
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision=COMMIT_SHA))

    assert isinstance(result, RepositoryWorkflowSnapshot)
    assert [source.path for source in result.sources] == [".github/workflows/ci.yml"]
    assert [(failure.path, failure.reason) for failure in result.failures] == [
        (".github/workflows/linked.yaml", "unsupported_object")
    ]


def test_reader_preserves_a_workflow_name_beyond_the_former_full_path_bound() -> None:
    long_name = f"{'a' * 240}.yml"

    async def handler(request: httpx.Request) -> httpx.Response:
        return _discovery_response(
            request,
            workflow_entries=(_blob_entry(long_name, BLOB_SHA, len(CONTENT)),),
        )

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision=COMMIT_SHA))

    assert isinstance(result, RepositoryWorkflowSnapshot)
    assert result.sources[0].path == f".github/workflows/{long_name}"


@pytest.mark.parametrize(
    ("registry_content", "expected_status"),
    (
        (REGISTRY_CONTENT, "available"),
        (b"{}", "invalid"),
    ),
)
def test_reader_projects_target_registry_from_the_same_commit_tree(
    registry_content: bytes,
    expected_status: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _discovery_response(request, registry_content=registry_content)

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision=COMMIT_SHA))

    assert isinstance(result, RepositoryWorkflowSnapshot)
    assert result.target_projection.status == expected_status
    if expected_status == "available":
        assert result.target_projection.registry_hash is not None
        workflow = result.target_projection.workflows[0]
        assert workflow.workflow_path == ".github/workflows/ci.yml"
        assert workflow.execution_jobs[0].needs == ("plan",)
    else:
        assert result.target_projection.registry_hash is None
        assert result.target_projection.workflows == ()


def test_truncated_tree_never_becomes_empty_or_complete() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
            return _response(_json({"sha": ROOT_TREE_SHA, "truncated": True, "tree": []}))
        return _discovery_response(request)

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision=COMMIT_SHA))

    assert result == WorkflowDiscoveryUnavailable("source_tree_limit_exceeded")


@pytest.mark.parametrize(
    ("file_count", "declared_size"),
    ((65, len(CONTENT)), (1, 262_145), (17, 262_144)),
    ids=("file-count", "single-file-bytes", "aggregate-bytes"),
)
def test_source_limits_fail_closed_before_blob_fetch(
    file_count: int,
    declared_size: int,
) -> None:
    workflow_entries = tuple(
        _blob_entry(f"workflow-{index}.yml", BLOB_SHA, declared_size) for index in range(file_count)
    )
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _discovery_response(request, workflow_entries=workflow_entries)

    factory = _factory(_private_key(), httpx.MockTransport(handler))
    result = asyncio.run(_read(factory, revision=COMMIT_SHA))

    assert result == WorkflowDiscoveryUnavailable("source_limit_exceeded")
    assert not any("/git/blobs/" in path for path in requests)


async def _read(
    factory: GitHubAppTransportFactory,
    *,
    revision: str | None = None,
) -> SnapshotReadOutcome:
    try:
        return await GitHubWorkflowSnapshotReader(factory).read(
            scope=RepositoryScope(77, 501),
            revision=revision,
        )
    finally:
        await factory.aclose()


def _discovery_response(
    request: httpx.Request,
    *,
    workflow_entries: tuple[dict[str, object], ...] | None = None,
    registry_content: bytes | None = None,
) -> httpx.Response:
    path = request.url.path
    if path.endswith("/access_tokens"):
        return _token_response()
    if path == "/repositories/501":
        return _response(
            _json(
                {
                    "id": 501,
                    "owner": {"login": "example"},
                    "name": "repo",
                    "full_name": "example/repo",
                    "default_branch": "master",
                }
            )
        )
    if path.endswith("/git/ref/heads/master"):
        return _response(
            _json({"ref": "refs/heads/master", "object": {"type": "commit", "sha": COMMIT_SHA}})
        )
    if path.endswith(f"/git/commits/{COMMIT_SHA}"):
        return _response(_json({"sha": COMMIT_SHA, "tree": {"sha": ROOT_TREE_SHA}}))
    if path.endswith(f"/git/trees/{ROOT_TREE_SHA}"):
        root_entries = [_tree_entry(".github", GITHUB_TREE_SHA)]
        if registry_content is not None:
            root_entries.append(_tree_entry(".ci-coordinator", TARGET_TREE_SHA))
        return _tree(ROOT_TREE_SHA, root_entries)
    if path.endswith(f"/git/trees/{GITHUB_TREE_SHA}"):
        return _tree(GITHUB_TREE_SHA, [_tree_entry("workflows", WORKFLOWS_TREE_SHA)])
    if path.endswith(f"/git/trees/{WORKFLOWS_TREE_SHA}"):
        resolved_entries = workflow_entries or (_blob_entry("ci.yml", BLOB_SHA, len(CONTENT)),)
        return _tree(WORKFLOWS_TREE_SHA, resolved_entries)
    if path.endswith(f"/git/trees/{TARGET_TREE_SHA}") and registry_content is not None:
        registry_sha = git_blob_sha1(registry_content)
        return _tree(
            TARGET_TREE_SHA,
            [_blob_entry("execution-registry.v1.json", registry_sha, len(registry_content))],
        )
    if path.endswith(f"/git/blobs/{BLOB_SHA}"):
        return _response(
            _json(
                {
                    "sha": BLOB_SHA,
                    "size": len(CONTENT),
                    "encoding": "base64",
                    "content": base64.b64encode(CONTENT).decode(),
                }
            )
        )
    if registry_content is not None:
        registry_sha = git_blob_sha1(registry_content)
        if path.endswith(f"/git/blobs/{registry_sha}"):
            return _response(
                _json(
                    {
                        "sha": registry_sha,
                        "size": len(registry_content),
                        "encoding": "base64",
                        "content": base64.b64encode(registry_content).decode(),
                    }
                )
            )
    raise AssertionError(f"unexpected GitHub request: {path}")


def _tree(sha: str, entries: object) -> httpx.Response:
    return _response(_json({"sha": sha, "truncated": False, "tree": entries}))


def _tree_entry(name: str, sha: str) -> dict[str, object]:
    return {"path": name, "mode": "040000", "type": "tree", "sha": sha}


def _blob_entry(
    name: str,
    sha: str,
    size: int,
    *,
    mode: str = "100644",
) -> dict[str, object]:
    return {"path": name, "mode": mode, "type": "blob", "sha": sha, "size": size}


def _response(body: bytes, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={
            "content-type": "application/json",
            "x-github-api-version-selected": GITHUB_API_VERSION,
        },
        content=body,
    )


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()
