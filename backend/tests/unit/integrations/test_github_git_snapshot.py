from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.adapter_snapshot import (
    GitHubAdapterSnapshotLoader,
)
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.git_snapshot import (
    GitHubGitSnapshotReader,
    regular_blob_entry,
    tree_entry,
)
from ci_coordinator.integrations.github.workflow_discovery_client import (
    WorkflowDiscoveryClient,
)
from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    GitTree,
    GitTreeEntry,
)

REPOSITORY = GitHubRepository("example-org", "target")
REVISION_SHA = "1" * 40
OBJECT_SHA = "2" * 40
CONTROL_TREE_SHA = "3" * 40


class _NoIoTransport:
    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        raise AssertionError(f"unexpected provider I/O: {request.operation}")


class _UnavailableTransport:
    def __init__(self) -> None:
        self.requests: list[GitHubRequest] = []

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        return GitHubResponse(
            status=404,
            api_version=GITHUB_API_VERSION,
            headers=(),
            body=b"not found",
            pagination=GitHubPaginationEvidence.not_paginated(),
        )


class _QueueTransport:
    def __init__(self, responses: tuple[GitHubResponse, ...]) -> None:
        self._responses = list(responses)
        self.requests: list[GitHubRequest] = []

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        return self._responses.pop(0)


def _entry(
    *,
    object_type: str,
    mode: str,
    size: int | None,
) -> GitTreeEntry:
    return GitTreeEntry("target", mode, object_type, OBJECT_SHA, size)


def test_snapshot_reader_requires_the_exact_protocol_client() -> None:
    with pytest.raises(TypeError, match="exact Git client"):
        GitHubGitSnapshotReader(cast(Any, object()))


@pytest.mark.parametrize(
    ("repository", "revision_sha", "expected_exception"),
    [
        (cast(Any, object()), REVISION_SHA, TypeError),
        (REPOSITORY, "", ValueError),
        (REPOSITORY, "A" * 40, ValueError),
        (REPOSITORY, "a" * 39, ValueError),
        (REPOSITORY, "a" * 41, ValueError),
        (REPOSITORY, cast(Any, 1), ValueError),
    ],
    ids=["repository-type", "empty", "uppercase", "short", "long", "revision-type"],
)
def test_root_tree_rejects_ambiguous_revision_identity_before_provider_io(
    repository: GitHubRepository,
    revision_sha: str,
    expected_exception: type[Exception],
) -> None:
    with pytest.raises(expected_exception):
        asyncio.run(_reader().root_tree(repository, revision_sha))


@pytest.mark.parametrize(
    ("path", "maximum_bytes"),
    [
        ("", 1),
        (cast(Any, 1), 1),
        (r"directory\file", 1),
        ("/file", 1),
        ("file/", 1),
        ("directory//file", 1),
        (".", 1),
        ("..", 1),
        ("directory/../file", 1),
        ("/".join(("directory",) * 17), 1),
        ("x" * 257, 1),
        ("/".join(("x" * 256,) * 4), 1),
        ("file", cast(Any, True)),
        ("file", -1),
    ],
    ids=[
        "empty",
        "path-type",
        "backslash",
        "leading-separator",
        "trailing-separator",
        "empty-segment",
        "current-directory",
        "parent-directory",
        "nested-parent-directory",
        "depth",
        "segment-bytes",
        "path-bytes",
        "maximum-type",
        "negative-maximum",
    ],
)
def test_regular_file_rejects_paths_outside_the_bounded_profile_before_provider_io(
    path: str,
    maximum_bytes: int,
) -> None:
    with pytest.raises(ValueError, match="bounded profile"):
        asyncio.run(
            _reader().regular_file(
                REPOSITORY,
                revision_sha=REVISION_SHA,
                path=path,
                maximum_bytes=maximum_bytes,
            )
        )


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ((), None),
        ((_entry(object_type="tree", mode="040000", size=None),), None),
        ((_entry(object_type="blob", mode="120000", size=1),), None),
        ((_entry(object_type="blob", mode="100644", size=None),), None),
        ((_entry(object_type="blob", mode="100644", size=2),), None),
        (
            (_entry(object_type="blob", mode="100644", size=1),),
            _entry(object_type="blob", mode="100644", size=1),
        ),
        (
            (_entry(object_type="blob", mode="100755", size=1),),
            _entry(object_type="blob", mode="100755", size=1),
        ),
    ],
    ids=[
        "absent",
        "directory",
        "symlink",
        "missing-size",
        "oversized",
        "regular",
        "executable",
    ],
)
def test_regular_blob_entry_admits_only_bounded_regular_files(
    entries: tuple[GitTreeEntry, ...],
    expected: GitTreeEntry | None,
) -> None:
    tree = GitTree(entries, False)

    assert tree_entry(tree, "target") == (entries[0] if entries else None)
    assert regular_blob_entry(tree, "target", maximum_bytes=1) == expected


@pytest.mark.parametrize(
    "entries",
    [
        (),
        (_entry(object_type="blob", mode="100644", size=1),),
        (_entry(object_type="tree", mode="100644", size=None),),
    ],
    ids=["absent", "blob", "wrong-directory-mode"],
)
def test_child_tree_rejects_non_directory_entries_without_provider_io(
    entries: tuple[GitTreeEntry, ...],
) -> None:
    result = asyncio.run(_reader().child_tree(REPOSITORY, GitTree(entries, False), "target"))

    assert result is None


def test_blob_without_provider_size_is_not_requested() -> None:
    entry = _entry(object_type="blob", mode="100644", size=None)

    result = asyncio.run(_reader().blob(REPOSITORY, entry, maximum_bytes=1))

    assert result is None


def test_unavailable_commit_or_tree_cannot_become_file_authority() -> None:
    transport = _UnavailableTransport()
    reader = GitHubGitSnapshotReader(
        WorkflowDiscoveryClient(transport, api_version=GITHUB_API_VERSION)
    )

    file_content = asyncio.run(
        reader.regular_file(
            REPOSITORY,
            revision_sha=REVISION_SHA,
            path="target",
            maximum_bytes=1,
        )
    )
    tree = asyncio.run(reader.tree(REPOSITORY, OBJECT_SHA))

    assert file_content is None
    assert tree is None
    assert [request.operation for request in transport.requests] == [
        "workflow_discovery.get_commit",
        "workflow_discovery.get_tree",
    ]


def test_missing_child_directory_cannot_become_file_authority() -> None:
    transport = _QueueTransport(
        (
            _json_response({"sha": REVISION_SHA, "tree": {"sha": OBJECT_SHA}}),
            _json_response({"sha": OBJECT_SHA, "truncated": False, "tree": []}),
        )
    )
    reader = GitHubGitSnapshotReader(
        WorkflowDiscoveryClient(transport, api_version=GITHUB_API_VERSION)
    )

    result = asyncio.run(
        reader.regular_file(
            REPOSITORY,
            revision_sha=REVISION_SHA,
            path="directory/target",
            maximum_bytes=1,
        )
    )

    assert result is None
    assert [request.operation for request in transport.requests] == [
        "workflow_discovery.get_commit",
        "workflow_discovery.get_tree",
    ]


@pytest.mark.parametrize(
    "stage",
    ["unavailable-root", "missing-control-directory", "missing-registry"],
)
def test_adapter_loader_rejects_incomplete_git_object_roots(stage: str) -> None:
    transport: _UnavailableTransport | _QueueTransport
    if stage == "unavailable-root":
        transport = _UnavailableTransport()
    else:
        root_entries = (
            []
            if stage == "missing-control-directory"
            else [
                {
                    "path": ".ci-coordinator",
                    "mode": "040000",
                    "type": "tree",
                    "sha": CONTROL_TREE_SHA,
                }
            ]
        )
        responses = [
            _json_response({"sha": REVISION_SHA, "tree": {"sha": OBJECT_SHA}}),
            _json_response(
                {
                    "sha": OBJECT_SHA,
                    "truncated": False,
                    "tree": root_entries,
                }
            ),
        ]
        if stage == "missing-registry":
            responses.append(
                _json_response(
                    {
                        "sha": CONTROL_TREE_SHA,
                        "truncated": False,
                        "tree": [],
                    }
                )
            )
        transport = _QueueTransport(tuple(responses))

    result = asyncio.run(
        GitHubAdapterSnapshotLoader(
            WorkflowDiscoveryClient(transport, api_version=GITHUB_API_VERSION)
        ).load(REPOSITORY, revision_sha=REVISION_SHA)
    )

    assert result is None


def test_adapter_loader_rejects_ambiguous_authority_before_provider_io() -> None:
    with pytest.raises(TypeError, match="exact Git client"):
        GitHubAdapterSnapshotLoader(cast(Any, object()))

    loader = GitHubAdapterSnapshotLoader(_client())
    with pytest.raises(TypeError, match="exact repository"):
        asyncio.run(
            loader.load(
                cast(Any, object()),
                revision_sha=REVISION_SHA,
            )
        )
    with pytest.raises(ValueError, match="immutable revision"):
        asyncio.run(loader.load(REPOSITORY, revision_sha="A" * 40))


def _reader() -> GitHubGitSnapshotReader:
    return GitHubGitSnapshotReader(_client())


def _client() -> WorkflowDiscoveryClient:
    return WorkflowDiscoveryClient(_NoIoTransport(), api_version=GITHUB_API_VERSION)


def _json_response(value: object) -> GitHubResponse:
    return GitHubResponse(
        status=200,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=json.dumps(value, separators=(",", ":")).encode(),
        pagination=GitHubPaginationEvidence.not_paginated(),
    )
