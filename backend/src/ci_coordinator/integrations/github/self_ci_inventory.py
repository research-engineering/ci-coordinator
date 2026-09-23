from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.git_snapshot import GitHubGitSnapshotReader
from ci_coordinator.integrations.github.workflow_discovery_client import WorkflowDiscoveryClient
from ci_coordinator.integrations.github.workflow_discovery_decoding import GitTreeEntry
from ci_coordinator.kernel import StrictJsonError, canonical_json, load_strict_json
from ci_coordinator.repo_context import DependencyGraphArtifact
from ci_coordinator.repo_context.freshness import is_safe_relative_path
from ci_coordinator.workflow_authority.git_objects import git_blob_oid

SELF_CI_GENERATOR: Final = "self-ci@1"
_REPORT_PATH: Final = ".ci-coordinator/self-ci-inventory.v1.json"
_GRAPH_PATH: Final = ".ci-coordinator/dependency-graph.v1.json"
_MAX_FILES: Final = 16_384
_MAX_TREES: Final = 512
_MAX_DEPTH: Final = 16
_MAX_FILE_BYTES: Final = 16 * 1024 * 1024
_MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
_MAX_REPORT_BYTES: Final = 1_048_576
_TIMEOUT_SECONDS: Final = 45


@dataclass(frozen=True)
class _Inventory:
    modes: Mapping[str, int]
    entries: Mapping[str, GitTreeEntry]


async def self_ci_inventory_is_current(
    client: WorkflowDiscoveryClient,
    repository: GitHubRepository,
    *,
    head_sha: str,
    graph_content: bytes,
    artifact: DependencyGraphArtifact,
) -> bool:
    if artifact.provenance.generator != SELF_CI_GENERATOR:
        raise ValueError("self CI inventory qualification requires the exact generator profile")
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            reader = GitHubGitSnapshotReader(client)
            inventory = await _load_inventory(reader, repository, head_sha)
            if inventory is None:
                return False
            graph_entry = inventory.entries.get(_GRAPH_PATH)
            report_entry = inventory.entries.get(_REPORT_PATH)
            if (
                graph_entry is None
                or graph_entry.object_sha != git_blob_oid(graph_content)
                or report_entry is None
                or report_entry.size is None
                or report_entry.size > _MAX_REPORT_BYTES
            ):
                return False
            report = await reader.blob(repository, report_entry, maximum_bytes=_MAX_REPORT_BYTES)
            return report is not None and _inventory_matches(report, inventory, artifact)
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


async def _load_inventory(
    reader: GitHubGitSnapshotReader, repository: GitHubRepository, head_sha: str
) -> _Inventory | None:
    trees = await reader.recursive_tree(
        repository, head_sha, maximum_entries=_MAX_FILES + _MAX_TREES
    )
    if trees is None or len(trees) > _MAX_TREES:
        return None
    entries: dict[str, GitTreeEntry] = {}
    total_bytes = 0
    for directory, tree in trees.items():
        depth = len(directory.split("/")) if directory else 0
        if depth > _MAX_DEPTH:
            return None
        prefix = directory + "/" if directory else ""
        for entry in tree.entries:
            path = prefix + entry.name
            if (
                not is_safe_relative_path(path)
                or len(path.encode("utf-8")) > 4096
                or any(ord(character) < 32 or ord(character) == 127 for character in path)
            ):
                return None
            if entry.object_type == "tree" and entry.mode == "040000":
                continue
            if (
                entry.object_type != "blob"
                or entry.mode not in {"100644", "100755"}
                or entry.size is None
                or entry.size > _MAX_FILE_BYTES
                or path in entries
            ):
                return None
            entries[path] = entry
            total_bytes += entry.size
            if len(entries) > _MAX_FILES or total_bytes > _MAX_TOTAL_BYTES:
                return None
    return _Inventory(
        modes={path: int(entry.mode, 8) for path, entry in entries.items()}, entries=entries
    )


def _inventory_matches(
    report_content: bytes, inventory: _Inventory, artifact: DependencyGraphArtifact
) -> bool:
    try:
        report = load_strict_json(report_content, max_bytes=_MAX_REPORT_BYTES)
    except StrictJsonError:
        return False
    if (
        not isinstance(report, dict)
        or type(report.get("schemaVersion")) is not int
        or report["schemaVersion"] != 1
        or report.get("profileId") != "ci-coordinator.self-ci-qualification"
        or not isinstance(source := report.get("responsibilityInventory"), dict)
        or source.get("scope") != "git-observed-regular-paths-and-modes"
        or type(source.get("pathCount")) is not int
        or source["pathCount"] != len(inventory.modes)
        or not inventory.modes
        or not isinstance(digest := source.get("pathInventorySha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or len(artifact.nodes) != len(inventory.modes)
        or {node.path for node in artifact.nodes} != inventory.modes.keys()
    ):
        return False
    records = [[path, mode] for path, mode in sorted(inventory.modes.items())]
    return hashlib.sha256(canonical_json(records)).hexdigest() == digest
