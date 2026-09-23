from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel import utf16_sort_key

type FileChangeStatus = Literal[
    "added",
    "modified",
    "removed",
    "renamed",
    "copied",
    "changed",
    "unknown",
]

_FILE_CHANGE_STATUSES = frozenset(
    {"added", "modified", "removed", "renamed", "copied", "changed", "unknown"}
)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True, slots=True)
class RepositoryEpoch:
    installation_id: int
    repository_id: int
    owner: str
    name: str
    event_name: Literal["pull_request", "push", "merge_group"]
    ref: str
    base_sha: str
    head_sha: str

    def __post_init__(self) -> None:
        for field_name, integer_value in (
            ("installation_id", self.installation_id),
            ("repository_id", self.repository_id),
        ):
            if type(integer_value) is not int or integer_value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name, string_value in (
            ("owner", self.owner),
            ("name", self.name),
            ("ref", self.ref),
        ):
            if type(string_value) is not str or not string_value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.event_name not in {"pull_request", "push", "merge_group"}:
            raise ValueError("event_name is not admitted for deterministic planning")
        _require_git_object_id(self.base_sha, field_name="base_sha")
        _require_git_object_id(self.head_sha, field_name="head_sha")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.installation_id,
            "repositoryId": self.repository_id,
            "owner": self.owner,
            "name": self.name,
            "event": self.event_name,
            "ref": self.ref,
            "baseSha": self.base_sha,
            "headSha": self.head_sha,
        }


@dataclass(frozen=True, slots=True)
class DiffSource:
    provider: Literal["github"]
    complete: bool
    page_count: int
    file_count: int
    max_files: int

    def __post_init__(self) -> None:
        if self.provider != "github":
            raise ValueError("provider must be github")
        if type(self.complete) is not bool:
            raise ValueError("complete must be a boolean")
        for field_name, value, minimum in (
            ("page_count", self.page_count, 0),
            ("file_count", self.file_count, 0),
            ("max_files", self.max_files, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field_name} must be an integer >= {minimum}")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "complete": self.complete,
            "pageCount": self.page_count,
            "fileCount": self.file_count,
            "maxFiles": self.max_files,
        }


@dataclass(frozen=True, slots=True)
class FileDelta:
    path: str
    status: FileChangeStatus
    additions: int
    deletions: int
    previous_path: str | None = None
    patch_hash: str | None = None

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path:
            raise ValueError("path must be a non-empty string")
        if self.status not in _FILE_CHANGE_STATUSES:
            raise ValueError("status must be an admitted file-change status")
        if self.previous_path is not None and (
            type(self.previous_path) is not str or not self.previous_path
        ):
            raise ValueError("previous_path must be absent or a non-empty string")
        for field_name, value in (("additions", self.additions), ("deletions", self.deletions)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.patch_hash is not None and _SHA256_HEX.fullmatch(self.patch_hash) is None:
            raise ValueError("patch_hash must be lowercase SHA-256 hexadecimal")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "previousPath": self.previous_path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "patchHash": self.patch_hash,
        }


@dataclass(frozen=True, slots=True)
class DiffContext:
    base_sha: str
    head_sha: str
    files: tuple[FileDelta, ...]
    source: DiffSource
    truncated: bool
    invalidating_reasons: tuple[str, ...]
    diff_hash: str

    def __post_init__(self) -> None:
        _require_git_object_id(self.base_sha, field_name="base_sha")
        _require_git_object_id(self.head_sha, field_name="head_sha")
        if type(self.truncated) is not bool:
            raise ValueError("truncated must be a boolean")
        _require_sorted_unique(self.invalidating_reasons, field_name="invalidating_reasons")
        if _SHA256_HEX.fullmatch(self.diff_hash) is None:
            raise ValueError("diff_hash must be lowercase SHA-256 hexadecimal")

    @property
    def full_ci_invalidating(self) -> bool:
        return bool(self.invalidating_reasons)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        paths = {file.path for file in self.files}
        paths.update(file.previous_path for file in self.files if file.previous_path is not None)
        return tuple(sorted(paths, key=utf16_sort_key))

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "baseSha": self.base_sha,
            "headSha": self.head_sha,
            "truncated": self.truncated,
            "source": self.source.to_identity_mapping(),
            "files": [file.to_identity_mapping() for file in self.files],
        }


def _require_git_object_id(value: object, *, field_name: str) -> None:
    if type(value) is not str or _GIT_OBJECT_ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 40..64 lowercase hexadecimal characters")


def _require_sorted_unique(values: tuple[str, ...], *, field_name: str) -> None:
    if any(type(value) is not str or not value for value in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be sorted and unique by ECMAScript UTF-16 order")
