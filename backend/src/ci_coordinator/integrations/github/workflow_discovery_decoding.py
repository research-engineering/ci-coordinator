"""Strict endpoint decoders for content-addressed workflow discovery."""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import TypeGuard

from ci_coordinator.integrations.github._response_decoding import (
    canonical_text_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
    repository_component_is_admitted,
)
from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.kernel import (
    StrictJsonError,
    git_branch_name_is_admitted,
    load_strict_json,
    utf16_sort_key,
)

GITHUB_OBJECT_ID_LENGTH = 40


@dataclass(frozen=True, slots=True)
class DiscoveryRepository:
    repository_id: int
    repository: GitHubRepository
    default_branch: str


@dataclass(frozen=True, slots=True)
class GitCommit:
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True, slots=True)
class GitTreeEntry:
    name: str
    mode: str
    object_type: str
    object_sha: str
    size: int | None


@dataclass(frozen=True, slots=True)
class GitTree:
    entries: tuple[GitTreeEntry, ...]
    limit_exceeded: bool


@dataclass(frozen=True, slots=True)
class GitBlob:
    blob_sha: str
    content: bytes


def is_github_object_id(value: object) -> TypeGuard[str]:
    return (
        type(value) is str
        and len(value) == GITHUB_OBJECT_ID_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def decode_discovery_repository(
    body: bytes,
    *,
    max_json_bytes: int,
) -> DiscoveryRepository | None:
    value = _json_object(body, max_json_bytes=max_json_bytes)
    if value is None:
        return None
    repository_id = positive_safe_integer(value.get("id"))
    name = canonical_text_or_none(value.get("name"), maximum_bytes=512)
    full_name = canonical_text_or_none(value.get("full_name"), maximum_bytes=1_025)
    default_branch = canonical_text_or_none(value.get("default_branch"), maximum_bytes=1_024)
    owner = object_or_none(value.get("owner"))
    owner_login = (
        None if owner is None else canonical_text_or_none(owner.get("login"), maximum_bytes=512)
    )
    if (
        repository_id is None
        or name is None
        or owner_login is None
        or not repository_component_is_admitted(name)
        or not repository_component_is_admitted(owner_login)
        or full_name != f"{owner_login}/{name}"
        or default_branch is None
        or not git_branch_name_is_admitted(default_branch)
    ):
        return None
    try:
        return DiscoveryRepository(
            repository_id,
            GitHubRepository(owner_login, name),
            default_branch,
        )
    except ValueError:
        return None


def decode_git_reference(
    body: bytes,
    *,
    expected_ref: str,
    max_json_bytes: int,
) -> str | None:
    value = _json_object(body, max_json_bytes=max_json_bytes)
    target = None if value is None else object_or_none(value.get("object"))
    if (
        value is None
        or value.get("ref") != f"refs/{expected_ref}"
        or target is None
        or target.get("type") != "commit"
    ):
        return None
    sha = target.get("sha")
    return sha if is_github_object_id(sha) else None


def decode_git_commit(
    body: bytes,
    *,
    expected_commit_sha: str,
    max_json_bytes: int,
) -> GitCommit | None:
    value = _json_object(body, max_json_bytes=max_json_bytes)
    tree = None if value is None else object_or_none(value.get("tree"))
    tree_sha = None if tree is None else tree.get("sha")
    if (
        value is None
        or value.get("sha") != expected_commit_sha
        or not is_github_object_id(expected_commit_sha)
        or not is_github_object_id(tree_sha)
    ):
        return None
    return GitCommit(expected_commit_sha, tree_sha)


def decode_git_tree(
    body: bytes,
    *,
    expected_tree_sha: str,
    max_json_bytes: int,
    max_entries: int,
) -> GitTree | None:
    value = _json_object(body, max_json_bytes=max_json_bytes)
    raw_entries = None if value is None else value.get("tree")
    if (
        value is None
        or value.get("sha") != expected_tree_sha
        or not is_github_object_id(expected_tree_sha)
        or type(value.get("truncated")) is not bool
        or type(raw_entries) is not list
        or type(max_entries) is not int
        or max_entries < 1
    ):
        return None
    truncated = value["truncated"]
    if truncated is True or len(raw_entries) > max_entries:
        return GitTree((), True)

    entries: list[GitTreeEntry] = []
    for raw_entry in raw_entries:
        entry = object_or_none(raw_entry)
        if entry is None:
            return None
        name = canonical_text_or_none(entry.get("path"), maximum_bytes=256)
        mode = canonical_text_or_none(entry.get("mode"), maximum_bytes=6)
        object_type = canonical_text_or_none(entry.get("type"), maximum_bytes=6)
        object_sha = entry.get("sha")
        raw_size = entry.get("size")
        size = None if raw_size is None else non_negative_safe_integer(raw_size)
        if (
            name is None
            or "/" in name
            or mode not in {"040000", "100644", "100755", "120000", "160000"}
            or object_type not in {"blob", "commit", "tree"}
            or not is_github_object_id(object_sha)
            or not _coherent_tree_entry(mode, object_type, size)
            or (raw_size is not None and size is None)
        ):
            return None
        entries.append(GitTreeEntry(name, mode, object_type, object_sha, size))

    ordered = tuple(sorted(entries, key=lambda item: utf16_sort_key(item.name)))
    if len({entry.name for entry in ordered}) != len(ordered):
        return None
    return GitTree(ordered, False)


def decode_git_blob(
    body: bytes,
    *,
    expected_blob_sha: str,
    expected_size: int,
    max_json_bytes: int,
    max_content_bytes: int,
) -> GitBlob | None:
    value = _json_object(body, max_json_bytes=max_json_bytes)
    if value is None:
        return None
    size = non_negative_safe_integer(value.get("size"))
    content = value.get("content")
    if (
        value.get("sha") != expected_blob_sha
        or not is_github_object_id(expected_blob_sha)
        or size != expected_size
        or value.get("encoding") != "base64"
        or type(content) is not str
    ):
        return None
    decoded = _decode_base64(
        content,
        expected_size=expected_size,
        max_content_bytes=max_content_bytes,
    )
    if decoded is None or _git_blob_sha(decoded) != expected_blob_sha:
        return None
    return GitBlob(expected_blob_sha, decoded)


def _json_object(body: bytes, *, max_json_bytes: int) -> dict[str, object] | None:
    try:
        return object_or_none(load_strict_json(body, max_bytes=max_json_bytes))
    except StrictJsonError:
        return None


def _decode_base64(
    value: str,
    *,
    expected_size: int,
    max_content_bytes: int,
) -> bytes | None:
    if (
        not value.isascii()
        or expected_size > max_content_bytes
        or any(character.isspace() and character not in "\r\n" for character in value)
    ):
        return None
    compact = value.replace("\r", "").replace("\n", "")
    if len(compact) > 4 * ((max_content_bytes + 2) // 3):
        return None
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        return None
    return decoded if len(decoded) == expected_size else None


def _coherent_tree_entry(mode: str, object_type: str, size: int | None) -> bool:
    return (
        (object_type == "tree" and mode == "040000" and size is None)
        or (object_type == "commit" and mode == "160000" and size is None)
        or (object_type == "blob" and mode in {"100644", "100755", "120000"} and size is not None)
    )


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()
