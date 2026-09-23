"""Qualify a bounded recursive Git response against every native tree object ID."""

from __future__ import annotations

from typing import Final, cast

from ci_coordinator.integrations.github.workflow_discovery_decoding import (
    GitTree,
    decode_git_tree,
    is_github_object_id,
)
from ci_coordinator.kernel import StrictJsonError, canonical_json, load_strict_json
from ci_coordinator.workflow_authority.git_objects import git_tree_oid
from ci_coordinator.workflow_authority.model import GitObjectType, GitTreeChild

_MAX_JSON_BYTES: Final = 7 * 1024 * 1024
_MAX_DIRECT_ENTRIES: Final = 4096
_MAX_PATH_BYTES: Final = 4096
_MAX_PATH_COMPONENTS: Final = 17


def decode_recursive_git_tree(
    body: bytes, *, expected_tree_sha: str, maximum_entries: int
) -> dict[str, GitTree] | None:
    try:
        value = load_strict_json(body, max_bytes=_MAX_JSON_BYTES)
    except StrictJsonError:
        return None
    if (
        not isinstance(value, dict)
        or not is_github_object_id(expected_tree_sha)
        or value.get("sha") != expected_tree_sha
        or value.get("truncated") is not False
        or not isinstance(rows := value.get("tree"), list)
        or type(maximum_entries) is not int
        or maximum_entries < 1
        or len(rows) > maximum_entries
    ):
        return None
    groups: dict[str, list[dict[str, object]]] = {"": []}
    tree_oids = {"": expected_tree_sha}
    observed: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(path := row.get("path"), str):
            return None
        parts = path.split("/")
        if (
            path in observed
            or len(path.encode("utf-8")) > _MAX_PATH_BYTES
            or len(parts) > _MAX_PATH_COMPONENTS
            or "\\" in path
            or any(not part or part in {".", ".."} for part in parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
        ):
            return None
        observed.add(path)
        parent, _, name = path.rpartition("/")
        groups.setdefault(parent, []).append({**row, "path": name})
        if row.get("type") == "tree":
            oid = row.get("sha")
            if not is_github_object_id(oid):
                return None
            tree_oids[path] = oid
            groups.setdefault(path, [])
    if groups.keys() != tree_oids.keys():
        return None
    trees: dict[str, GitTree] = {}
    for path, children in groups.items():
        tree = decode_git_tree(
            canonical_json({"sha": tree_oids[path], "truncated": False, "tree": children}),
            expected_tree_sha=tree_oids[path],
            max_json_bytes=_MAX_JSON_BYTES,
            max_entries=_MAX_DIRECT_ENTRIES,
        )
        if tree is None or tree.limit_exceeded:
            return None
        objects = tuple(
            GitTreeChild(
                entry.name,
                entry.mode,
                cast(GitObjectType, entry.object_type),
                entry.object_sha,
                entry.size,
            )
            for entry in tree.entries
        )
        # A non-truncated flag alone cannot prove all descendants were returned.
        # Each reconstructed child list must hash to its parent-linked Git OID.
        if git_tree_oid(objects) != tree_oids[path]:
            return None
        trees[path] = tree
    return trees
