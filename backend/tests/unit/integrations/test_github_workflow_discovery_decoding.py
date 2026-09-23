from __future__ import annotations

import base64
import hashlib
import json

import pytest

from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    decode_discovery_repository,
    decode_git_blob,
    decode_git_commit,
    decode_git_reference,
    decode_git_tree,
    is_github_object_id,
)

COMMIT_SHA = "a" * 40
TREE_SHA = "b" * 40


def test_repository_reference_and_commit_form_one_exact_identity_chain() -> None:
    repository = decode_discovery_repository(
        _json(
            {
                "id": 501,
                "name": "service",
                "full_name": "example/service",
                "default_branch": "master",
                "owner": {"login": "example"},
            }
        ),
        max_json_bytes=1_024,
    )
    reference = decode_git_reference(
        _json({"ref": "refs/heads/master", "object": {"type": "commit", "sha": COMMIT_SHA}}),
        expected_ref="heads/master",
        max_json_bytes=512,
    )
    commit = decode_git_commit(
        _json({"sha": COMMIT_SHA, "tree": {"sha": TREE_SHA}}),
        expected_commit_sha=COMMIT_SHA,
        max_json_bytes=512,
    )

    assert repository is not None and repository.repository_id == 501
    assert repository.repository.path == "/repos/example/service"
    assert reference == COMMIT_SHA
    assert commit is not None and commit.tree_sha == TREE_SHA


@pytest.mark.parametrize(
    ("default_branch", "admitted"),
    [
        ("@", True),
        ("feature/release", True),
        ("/main", False),
        ("main/", False),
        ("../main", False),
        ("feature//main", False),
        (".hidden", False),
        ("main.lock", False),
        ("main..next", False),
        ("main@{1}", False),
        ("main\nnext", False),
        ("main~1", False),
        ("main\\next", False),
    ],
)
def test_repository_decoder_admits_only_canonical_git_branch_names(
    default_branch: str,
    admitted: bool,
) -> None:
    result = decode_discovery_repository(
        _json(
            {
                "id": 501,
                "name": "service",
                "full_name": "example/service",
                "default_branch": default_branch,
                "owner": {"login": "example"},
            }
        ),
        max_json_bytes=1_024,
    )

    assert (result is not None) is admitted


@pytest.mark.parametrize(
    ("owner", "name", "full_name"),
    [
        ("..", "service", "../service"),
        ("example", "..", "example/.."),
        ("example/platform", "service", "example/platform/service"),
        ("example\0", "service", "example\0/service"),
    ],
)
def test_repository_decoder_rejects_noncanonical_route_components(
    owner: str,
    name: str,
    full_name: str,
) -> None:
    result = decode_discovery_repository(
        _json(
            {
                "id": 501,
                "name": name,
                "full_name": full_name,
                "default_branch": "master",
                "owner": {"login": owner},
            }
        ),
        max_json_bytes=1_024,
    )

    assert result is None


@pytest.mark.parametrize("length", [39, 41, 64])
def test_github_object_id_profile_rejects_non_sha1_lengths(length: int) -> None:
    assert not is_github_object_id("a" * length)


def test_tree_decoder_requires_non_truncated_canonical_entries() -> None:
    result = decode_git_tree(
        _json(
            {
                "sha": TREE_SHA,
                "truncated": False,
                "tree": [
                    {"path": "z.yml", "mode": "100644", "type": "blob", "sha": "c" * 40, "size": 2},
                    {"path": "a.yml", "mode": "100644", "type": "blob", "sha": "d" * 40, "size": 1},
                ],
            }
        ),
        expected_tree_sha=TREE_SHA,
        max_json_bytes=2_048,
        max_entries=64,
    )

    assert result is not None
    assert [entry.name for entry in result.entries] == ["a.yml", "z.yml"]
    assert result.limit_exceeded is False


@pytest.mark.parametrize(
    ("truncated", "entries", "limit"),
    [
        (True, [], 64),
        (
            False,
            [
                {"path": str(index), "mode": "040000", "type": "tree", "sha": TREE_SHA}
                for index in range(3)
            ],
            2,
        ),
    ],
)
def test_tree_limit_is_explicit_and_never_returns_partial_entries(
    truncated: bool,
    entries: list[dict[str, object]],
    limit: int,
) -> None:
    result = decode_git_tree(
        _json({"sha": TREE_SHA, "truncated": truncated, "tree": entries}),
        expected_tree_sha=TREE_SHA,
        max_json_bytes=2_048,
        max_entries=limit,
    )

    assert result is not None
    assert result.limit_exceeded is True
    assert result.entries == ()


@pytest.mark.parametrize("field", ["sha", "size", "encoding", "content"])
def test_blob_decoder_binds_provider_fields_and_recomputes_git_identity(field: str) -> None:
    content = b"name: CI\n"
    blob_sha = _blob_sha(content)
    body: dict[str, object] = {
        "sha": blob_sha,
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii"),
    }
    replacements: dict[str, object] = {
        "sha": COMMIT_SHA,
        "size": len(content) + 1,
        "encoding": "utf-8",
        "content": base64.b64encode(b"name: CD\n").decode("ascii"),
    }
    body[field] = replacements[field]

    assert (
        decode_git_blob(
            _json(body),
            expected_blob_sha=blob_sha,
            expected_size=len(content),
            max_json_bytes=2_048,
            max_content_bytes=256,
        )
        is None
    )


def _blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()
