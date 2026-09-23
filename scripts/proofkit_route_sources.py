from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.bounded_git import capture_git_text
from scripts.proofkit_common import (
    JsonObject,
    as_array,
    js_json_dumps,
    parse_json_object,
    safe_repo_path,
)
from scripts.proofkit_common import exact_keys as _exact_keys
from scripts.proofkit_common import nonempty_string_array as _string_array
from scripts.proofkit_common import trimmed_text as _strict_text
from scripts.proofkit_route_contract import legacy_binding_projection
from scripts.repository_source_admission import read_bounded_repository_text

BINDING_PATH = "proofkit/requirement-bindings.json"
WITNESS_PLAN_PATH = "proofkit/witness-plan-input.json"
MAX_INDEX_BYTES = 256 * 1024
MAX_LEGACY_BINDING_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_COUNT = 32
MAX_REQUIREMENT_SOURCE_COUNT = 256
MAX_TOTAL_REQUIREMENT_SOURCE_BYTES = 16 * 1024 * 1024

_INDEX_KEYS = {
    "schemaVersion",
    "bindingId",
    "contractId",
    "sourceColumns",
    "sources",
    "nonClaims",
}
_SOURCE_COLUMNS = ("sourceId", "path", "sha256")
_SOURCE_ID = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class RouteAuthority:
    binding_projection: JsonObject
    compact: bool
    source_count: int


def load_route_authority(*, repo_root: Path, ref: str | None = None) -> RouteAuthority:
    commit = None if ref is None else _commit(repo_root, ref)
    root_text = _read_text(
        repo_root,
        BINDING_PATH,
        commit=commit,
        maximum_bytes=MAX_LEGACY_BINDING_BYTES,
    )
    root = parse_json_object(root_text, BINDING_PATH)
    if type(root.get("schemaVersion")) is int and root.get("schemaVersion") == 1:
        return RouteAuthority(binding_projection=root, compact=False, source_count=0)
    if len(root_text.encode()) > MAX_INDEX_BYTES:
        raise ValueError("proof route source index exceeds its byte bound")
    route_sources = _route_sources(repo_root, root, commit=commit)
    requirements = _requirement_sources(repo_root, commit=commit)
    witness_plan = _read_json(
        repo_root,
        WITNESS_PLAN_PATH,
        commit=commit,
        maximum_bytes=MAX_SOURCE_BYTES,
    )
    return RouteAuthority(
        binding_projection=legacy_binding_projection(
            route_sources,
            requirements,
            witness_plan,
        ),
        compact=True,
        source_count=len(route_sources),
    )


def _route_sources(
    repo_root: Path,
    source_index: Mapping[str, object],
    *,
    commit: str | None,
) -> list[tuple[str, str, JsonObject]]:
    _exact_keys(source_index, _INDEX_KEYS, "proof route source index")
    _literal(source_index, "schemaVersion", 2)
    _literal(source_index, "bindingId", "ci-coordinator.requirement-bindings")
    _literal(
        source_index,
        "contractId",
        "ci-coordinator.requirement-binding-route-index.v2",
    )
    columns = _string_array(source_index.get("sourceColumns"), "source index columns")
    if columns != list(_SOURCE_COLUMNS):
        raise ValueError("proof route source index columns are not canonical")
    _string_array(source_index.get("nonClaims"), "proof route source index nonClaims")
    rows = as_array(source_index.get("sources"), "proof route sources")
    if not 0 < len(rows) <= MAX_SOURCE_COUNT:
        raise ValueError("proof route source count is outside the admitted bound")
    sources: list[tuple[str, str, JsonObject]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    total_bytes = 0
    previous_source_id = ""
    for raw in rows:
        row = as_array(raw, "proof route source row")
        if len(row) != len(_SOURCE_COLUMNS):
            raise ValueError("proof route source row does not match sourceColumns")
        source_id = _strict_text(row[0], "proof route source id")
        if _SOURCE_ID.fullmatch(source_id) is None:
            raise ValueError(f"proof route source id is invalid: {source_id}")
        if source_id <= previous_source_id:
            raise ValueError("proof route source rows must be unique and sorted by sourceId")
        previous_source_id = source_id
        path = safe_repo_path(row[1])
        if not path.startswith("proofkit/routes/") or not path.endswith(".v2.json"):
            raise ValueError(f"proof route source path is outside its owner directory: {path}")
        digest = _strict_text(row[2], f"{source_id} sha256")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"proof route source digest is invalid: {source_id}")
        if source_id in seen_ids or path in seen_paths:
            raise ValueError("proof route source ids and paths must be unique")
        seen_ids.add(source_id)
        seen_paths.add(path)
        text = _read_text(repo_root, path, commit=commit, maximum_bytes=MAX_SOURCE_BYTES)
        total_bytes += len(text.encode())
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            raise ValueError("proof route sources exceed the aggregate byte bound")
        if hashlib.sha256(text.encode()).hexdigest() != digest:
            raise ValueError(f"proof route source digest drift: {source_id}")
        sources.append((source_id, path, parse_json_object(text, path)))
    return sources


def _requirement_sources(repo_root: Path, *, commit: str | None) -> list[tuple[str, JsonObject]]:
    if commit is None:
        output = capture_git_text(
            repo_root,
            (
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "docs/specs",
            ),
        )
    else:
        output = capture_git_text(
            repo_root,
            ("ls-tree", "-r", "--name-only", commit, "--", "docs/specs"),
        )
    paths = sorted(path for path in output.splitlines() if path.endswith("/requirements.v1.json"))
    if not paths:
        raise ValueError("no requirement sources were discovered")
    if len(paths) > MAX_REQUIREMENT_SOURCE_COUNT:
        raise ValueError("requirement source count exceeds its admitted bound")
    sources: list[tuple[str, JsonObject]] = []
    total_bytes = 0
    for path in paths:
        text = _read_text(repo_root, path, commit=commit, maximum_bytes=MAX_SOURCE_BYTES)
        total_bytes += len(text.encode())
        if total_bytes > MAX_TOTAL_REQUIREMENT_SOURCE_BYTES:
            raise ValueError("requirement sources exceed the aggregate byte bound")
        sources.append((path, parse_json_object(text, path)))
    return sources


def _read_json(
    repo_root: Path,
    path: str,
    *,
    commit: str | None,
    maximum_bytes: int,
) -> JsonObject:
    return parse_json_object(
        _read_text(repo_root, path, commit=commit, maximum_bytes=maximum_bytes),
        path,
    )


def _read_text(
    repo_root: Path,
    path: str,
    *,
    commit: str | None,
    maximum_bytes: int,
) -> str:
    relative = safe_repo_path(path)
    if commit is None:
        return read_bounded_repository_text(
            repo_root,
            PurePosixPath(relative),
            maximum_bytes=maximum_bytes,
        )
    value = capture_git_text(repo_root, ("show", f"{commit}:{relative}"))
    if len(value.encode()) > maximum_bytes:
        raise ValueError(f"repository source exceeds {maximum_bytes} bytes: {relative}")
    return value


def _commit(repo_root: Path, ref: str) -> str:
    return capture_git_text(
        repo_root,
        ("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"),
        strip=True,
    )


def _literal(value: Mapping[str, object], field: str, expected: object) -> None:
    if value.get(field) != expected or type(value.get(field)) is not type(expected):
        raise ValueError(f"proof route source index {field} must be {js_json_dumps(expected)}")
