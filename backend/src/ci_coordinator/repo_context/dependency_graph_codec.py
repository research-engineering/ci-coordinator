"""Bounded strict admission for target-repository dependency graphs."""

from __future__ import annotations

import re
from typing import Final, cast

from ci_coordinator.kernel import StrictJsonError, load_strict_json, utf16_sort_key
from ci_coordinator.repo_context.dependency_graph import (
    DependencyGraphArtifact,
    DependencyGraphNode,
    GraphProvenance,
)
from ci_coordinator.repo_context.freshness import is_safe_relative_path, validate_path_pattern

DEPENDENCY_GRAPH_SCHEMA_VERSION: Final = "dependency-graph/v1"
MAX_DEPENDENCY_GRAPH_BYTES: Final = 1_000_000
MAX_DEPENDENCY_GRAPH_NODES: Final = 100_000

_GENERATOR: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}@[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}")


def parse_dependency_graph_artifact(
    content: bytes,
    *,
    retrieved_for_sha: str,
    trusted: bool,
) -> DependencyGraphArtifact | None:
    try:
        if not _git_sha(retrieved_for_sha) or type(trusted) is not bool:
            return None
        root = _exact_object(
            load_strict_json(content, max_bytes=MAX_DEPENDENCY_GRAPH_BYTES),
            {"provenance", "invalidatesWhenChanged", "globalRiskPaths", "nodes"},
        )
        provenance = _exact_object(
            root["provenance"],
            {"source", "schemaVersion", "generator"},
        )
        source = provenance["source"]
        if (
            source not in {"configured", "generated"}
            or provenance["schemaVersion"] != DEPENDENCY_GRAPH_SCHEMA_VERSION
            or type(provenance["generator"]) is not str
            or _GENERATOR.fullmatch(provenance["generator"]) is None
        ):
            return None
        invalidators = _canonical_paths(
            root["invalidatesWhenChanged"],
            maximum_items=MAX_DEPENDENCY_GRAPH_NODES,
            maximum_length=512,
            patterns=True,
        )
        global_risk_paths = _canonical_paths(
            root["globalRiskPaths"],
            maximum_items=MAX_DEPENDENCY_GRAPH_NODES,
            maximum_length=512,
            patterns=True,
        )
        nodes = _nodes(root["nodes"])
        if invalidators is None or global_risk_paths is None or nodes is None:
            return None
        return DependencyGraphArtifact(
            provenance=GraphProvenance(
                source=source,
                schema_version=DEPENDENCY_GRAPH_SCHEMA_VERSION,
                generator=provenance["generator"],
                retrieved_for_sha=retrieved_for_sha,
                trusted=trusted,
                invalidates_when_changed=invalidators,
            ),
            nodes=nodes,
            global_risk_paths=global_risk_paths,
        )
    except (KeyError, StrictJsonError, TypeError, ValueError):
        return None


def _nodes(value: object) -> tuple[DependencyGraphNode, ...] | None:
    if type(value) is not list or len(value) > MAX_DEPENDENCY_GRAPH_NODES:
        return None
    nodes: list[DependencyGraphNode] = []
    for raw in value:
        node = _exact_object(raw, {"path", "dependents", "riskClasses"})
        path = _safe_path(node["path"], maximum_length=4_096)
        dependents = _canonical_paths(
            node["dependents"],
            maximum_items=MAX_DEPENDENCY_GRAPH_NODES,
            maximum_length=4_096,
            patterns=False,
        )
        risk_classes = _canonical_identifiers(node["riskClasses"])
        if path is None or dependents is None or risk_classes is None:
            return None
        nodes.append(DependencyGraphNode(path, dependents, risk_classes))
    node_paths = tuple(node.path for node in nodes)
    if node_paths != tuple(sorted(set(node_paths), key=utf16_sort_key)):
        return None
    known_paths = set(node_paths)
    if any(dependent not in known_paths for node in nodes for dependent in node.dependents):
        return None
    return tuple(nodes)


def _canonical_paths(
    value: object,
    *,
    maximum_items: int,
    maximum_length: int,
    patterns: bool,
) -> tuple[str, ...] | None:
    if type(value) is not list or len(value) > maximum_items:
        return None
    items: list[str] = []
    for raw in value:
        if type(raw) is not str or not 1 <= len(raw) <= maximum_length:
            return None
        invalid = (
            validate_path_pattern(raw) is not None if patterns else not is_safe_relative_path(raw)
        )
        if invalid:
            return None
        items.append(raw)
    result = tuple(items)
    return result if result == tuple(sorted(set(result), key=utf16_sort_key)) else None


def _canonical_identifiers(value: object) -> tuple[str, ...] | None:
    if type(value) is not list or len(value) > MAX_DEPENDENCY_GRAPH_NODES:
        return None
    result = tuple(value)
    if any(type(item) is not str or _IDENTIFIER.fullmatch(item) is None for item in result):
        return None
    return result if result == tuple(sorted(set(result), key=utf16_sort_key)) else None


def _safe_path(value: object, *, maximum_length: int) -> str | None:
    return (
        value
        if type(value) is str and 1 <= len(value) <= maximum_length and is_safe_relative_path(value)
        else None
    )


def _git_sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("dependency graph object shape is invalid")
    return cast(dict[str, object], value)
