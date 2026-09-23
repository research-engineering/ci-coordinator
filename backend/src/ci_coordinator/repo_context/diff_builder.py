from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.repo_context.diff_model import (
    DiffContext,
    DiffSource,
    FileChangeStatus,
    FileDelta,
)
from ci_coordinator.repo_context.freshness import is_safe_relative_path, is_unicode_scalar_string


@dataclass(frozen=True, slots=True)
class DiffFileChangeInput:
    path: object
    status: object
    additions: object = None
    deletions: object = None
    patch: object = None
    previous_path: object = None


@dataclass(frozen=True, slots=True)
class DiffBuildInput:
    base_sha: str
    head_sha: str
    files: tuple[DiffFileChangeInput, ...]
    source: DiffSource


def build_diff_context(input: DiffBuildInput) -> DiffContext:
    normalized = tuple(_normalize_file_change(item) for item in input.files)
    files = tuple(sorted((item.file for item in normalized), key=_file_sort_key))
    truncation_reasons = _truncation_reasons(input, files)
    unknown_reasons = {reason for item in normalized for reason in item.invalidating_reasons}
    reasons = tuple(sorted({*truncation_reasons, *unknown_reasons}, key=utf16_sort_key))
    truncated = bool(truncation_reasons)
    diff_hash = hash_object(
        {
            "baseSha": input.base_sha,
            "headSha": input.head_sha,
            "truncated": truncated,
            "source": input.source.to_identity_mapping(),
            "files": [file.to_identity_mapping() for file in files],
        }
    )
    return DiffContext(
        base_sha=input.base_sha,
        head_sha=input.head_sha,
        files=files,
        source=input.source,
        truncated=truncated,
        invalidating_reasons=reasons,
        diff_hash=diff_hash,
    )


@dataclass(frozen=True, slots=True)
class _NormalizedFileChange:
    file: FileDelta
    invalidating_reasons: tuple[str, ...]


def _normalize_file_change(input: DiffFileChangeInput) -> _NormalizedFileChange:
    reasons: set[str] = set()
    path = _path_or_sentinel(input.path, "missing_file_path", reasons)
    previous_path = _optional_path(input.previous_path, reasons)
    patch = _patch_or_none(input.patch, reasons)
    status = _normalize_status(input.status)
    if status == "unknown":
        reasons.add("unknown_file_status")
    path_safe = is_safe_relative_path(path)
    previous_path_safe = previous_path is None or is_safe_relative_path(previous_path)
    rename_evidence_known = status != "renamed" or previous_path is not None
    if not path_safe:
        reasons.add("unsafe_file_path")
        path = "__ci_coordinator_unknown_path__"
    if not previous_path_safe:
        reasons.add("unsafe_previous_file_path")
        previous_path = "__ci_coordinator_unknown_previous_path__"
    if not rename_evidence_known:
        reasons.add("incomplete_rename_evidence")
    if reasons:
        status = "unknown"
    return _NormalizedFileChange(
        file=FileDelta(
            path=path,
            previous_path=previous_path,
            status=status,
            additions=_non_negative_integer(input.additions),
            deletions=_non_negative_integer(input.deletions),
            patch_hash=hash_object({"patch": patch}) if patch is not None else None,
        ),
        invalidating_reasons=tuple(sorted(reasons, key=utf16_sort_key)),
    )


def _normalize_status(value: object) -> FileChangeStatus:
    if type(value) is str and value in {
        "added",
        "modified",
        "removed",
        "renamed",
        "copied",
        "changed",
    }:
        return cast(FileChangeStatus, value)
    return "unknown"


def _non_negative_integer(value: object) -> int:
    return value if type(value) is int and value > 0 else 0


def _path_or_sentinel(value: object, reason: str, reasons: set[str]) -> str:
    if type(value) is str and value:
        return value
    reasons.add(reason)
    return "__ci_coordinator_unknown_path__"


def _optional_path(value: object, reasons: set[str]) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is str:
        return value
    reasons.add("invalid_previous_file_path")
    return "__ci_coordinator_unknown_previous_path__"


def _patch_or_none(value: object, reasons: set[str]) -> str | None:
    if type(value) is not str or not value:
        return None
    if not is_unicode_scalar_string(value):
        reasons.add("unsafe_patch")
        return None
    return value


def _truncation_reasons(input: DiffBuildInput, files: tuple[FileDelta, ...]) -> set[str]:
    reasons: set[str] = set()
    source = input.source
    if not source.complete:
        reasons.add("diff_source_incomplete")
    if len(input.files) != source.file_count:
        reasons.add("diff_file_count_mismatch")
    if source.file_count > source.max_files or len(input.files) > source.max_files:
        reasons.add("diff_file_limit_exceeded")
    if len({_file_identity(file) for file in files}) != len(files):
        reasons.add("duplicate_file_identity")
    return reasons


def _file_sort_key(file: FileDelta) -> tuple[bytes, bytes, bytes]:
    return (
        utf16_sort_key(file.path),
        utf16_sort_key(file.previous_path or ""),
        utf16_sort_key(file.status),
    )


def _file_identity(file: FileDelta) -> tuple[str, str | None, FileChangeStatus]:
    return (file.path, file.previous_path, file.status)
