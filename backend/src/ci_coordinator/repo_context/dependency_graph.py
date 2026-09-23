from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.repo_context.diff_model import DiffContext, RepositoryEpoch
from ci_coordinator.repo_context.freshness import (
    is_safe_relative_path,
    matches_path_pattern,
    validate_path_pattern,
)
from ci_coordinator.repo_context.planning_input import PolicySnapshot


@dataclass(frozen=True, slots=True)
class GraphProvenance:
    source: Literal["configured", "generated"]
    schema_version: str
    generator: str
    retrieved_for_sha: str
    trusted: bool
    invalidates_when_changed: tuple[str, ...]

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "source": self.source,
            "schemaVersion": self.schema_version,
            "generator": self.generator,
            "retrievedForSha": self.retrieved_for_sha,
            "trusted": self.trusted,
            "invalidatesWhenChanged": list(self.invalidates_when_changed),
        }


@dataclass(frozen=True, slots=True)
class DependencyGraphNode:
    path: str
    dependents: tuple[str, ...]
    risk_classes: tuple[str, ...]

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "dependents": list(self.dependents),
            "riskClasses": list(self.risk_classes),
        }


@dataclass(frozen=True, slots=True)
class DependencyGraphArtifact:
    provenance: GraphProvenance
    nodes: tuple[DependencyGraphNode, ...]
    global_risk_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DependencyGraphContext:
    graph_hash: str
    provenance: GraphProvenance
    admitted_repo_epoch_hash: str
    admitted_diff_hash: str
    admitted_config_epoch_id: str
    admitted_policy_hash: str
    admitted_compiled_policy_hash: str
    fresh: bool
    nodes: tuple[DependencyGraphNode, ...]
    global_risk_paths: tuple[str, ...]
    invalidating_reasons: tuple[str, ...]
    unknown_path_mode: Literal["full-ci"] = "full-ci"

    def __post_init__(self) -> None:
        if type(self.fresh) is not bool or self.fresh != (not self.invalidating_reasons):
            raise ValueError("graph freshness must agree with invalidating reasons")
        if self.nodes != _normalized_nodes(self.nodes):
            raise ValueError("graph nodes must be canonical")
        if self.global_risk_paths != _sorted_unique(self.global_risk_paths):
            raise ValueError("graph global risk paths must be canonical")
        if self.invalidating_reasons != _sorted_unique(self.invalidating_reasons):
            raise ValueError("graph invalidating reasons must be canonical")
        if self.graph_hash != _graph_context_hash(
            provenance=self.provenance,
            admitted_repo_epoch_hash=self.admitted_repo_epoch_hash,
            admitted_diff_hash=self.admitted_diff_hash,
            admitted_config_epoch_id=self.admitted_config_epoch_id,
            admitted_policy_hash=self.admitted_policy_hash,
            admitted_compiled_policy_hash=self.admitted_compiled_policy_hash,
            fresh=self.fresh,
            nodes=self.nodes,
            global_risk_paths=self.global_risk_paths,
            invalidating_reasons=self.invalidating_reasons,
            unknown_path_mode=self.unknown_path_mode,
        ):
            raise ValueError("graph hash does not seal graph context coordinates")

    @property
    def full_ci_invalidating(self) -> bool:
        return bool(self.invalidating_reasons)


def build_dependency_graph(
    repo_epoch: RepositoryEpoch,
    diff: DiffContext,
    policy: PolicySnapshot,
    artifact: DependencyGraphArtifact,
) -> DependencyGraphContext:
    nodes = _normalized_nodes(artifact.nodes)
    artifact_global_risk_paths = _sorted_unique(artifact.global_risk_paths)
    global_risk_paths = policy.global_risk_paths
    provenance = _normalized_provenance(artifact.provenance)
    reasons = _freshness_reasons(
        repo_epoch,
        diff,
        policy,
        provenance,
        nodes,
        artifact_global_risk_paths,
    )
    fresh = not reasons
    invalidating_reasons = tuple(sorted(reasons, key=utf16_sort_key))
    admitted_repo_epoch_hash = hash_object(repo_epoch.to_identity_mapping())
    graph_hash = _graph_context_hash(
        provenance=provenance,
        admitted_repo_epoch_hash=admitted_repo_epoch_hash,
        admitted_diff_hash=diff.diff_hash,
        admitted_config_epoch_id=policy.epoch_id,
        admitted_policy_hash=policy.policy_hash,
        admitted_compiled_policy_hash=policy.compiled_policy_hash,
        fresh=fresh,
        nodes=nodes,
        global_risk_paths=global_risk_paths,
        invalidating_reasons=invalidating_reasons,
        unknown_path_mode="full-ci",
    )
    return DependencyGraphContext(
        graph_hash=graph_hash,
        provenance=provenance,
        admitted_repo_epoch_hash=admitted_repo_epoch_hash,
        admitted_diff_hash=diff.diff_hash,
        admitted_config_epoch_id=policy.epoch_id,
        admitted_policy_hash=policy.policy_hash,
        admitted_compiled_policy_hash=policy.compiled_policy_hash,
        fresh=fresh,
        nodes=nodes,
        global_risk_paths=global_risk_paths,
        invalidating_reasons=invalidating_reasons,
    )


def _normalized_nodes(nodes: tuple[DependencyGraphNode, ...]) -> tuple[DependencyGraphNode, ...]:
    normalized = (
        DependencyGraphNode(
            path=node.path,
            dependents=_sorted_unique(node.dependents),
            risk_classes=_sorted_unique(node.risk_classes),
        )
        for node in nodes
    )
    return tuple(sorted(normalized, key=lambda node: utf16_sort_key(node.path)))


def _normalized_provenance(provenance: GraphProvenance) -> GraphProvenance:
    return GraphProvenance(
        source=provenance.source,
        schema_version=provenance.schema_version,
        generator=provenance.generator,
        retrieved_for_sha=provenance.retrieved_for_sha,
        trusted=provenance.trusted,
        invalidates_when_changed=_sorted_unique(provenance.invalidates_when_changed),
    )


def _freshness_reasons(
    repo_epoch: RepositoryEpoch,
    diff: DiffContext,
    policy: PolicySnapshot,
    provenance: GraphProvenance,
    nodes: tuple[DependencyGraphNode, ...],
    artifact_global_risk_paths: tuple[str, ...],
) -> set[str]:
    reasons: set[str] = set()
    if repo_epoch.head_sha != diff.head_sha:
        reasons.add("repo_epoch_diff_mismatch")
    if not provenance.trusted:
        reasons.add("graph_provenance_untrusted")
    if not provenance.schema_version.strip() or not provenance.generator.strip():
        reasons.add("graph_provenance_incomplete")
    if provenance.retrieved_for_sha != diff.head_sha:
        reasons.add("graph_head_mismatch")
    if provenance.source != policy.dependency_graph_source:
        reasons.add("graph_source_mismatch")
    if artifact_global_risk_paths != policy.global_risk_paths:
        reasons.add("graph_global_risk_paths_mismatch")

    invalidators = _sorted_unique(provenance.invalidates_when_changed)
    if any(validate_path_pattern(pattern) is not None for pattern in invalidators):
        reasons.add("graph_invalidator_pattern_invalid")
    elif any(
        matches_path_pattern(pattern, path)
        for pattern in invalidators
        for path in diff.changed_paths
    ):
        reasons.add("graph_invalidated_by_diff")

    node_paths = tuple(node.path for node in nodes)
    if len(set(node_paths)) != len(node_paths):
        reasons.add("duplicate_graph_node")
    if any(not is_safe_relative_path(node.path) for node in nodes):
        reasons.add("unsafe_graph_node_path")
    valid_node_paths = set(node_paths)
    if any(not is_safe_relative_path(dependent) for node in nodes for dependent in node.dependents):
        reasons.add("unsafe_graph_edge")
    if any(dependent not in valid_node_paths for node in nodes for dependent in node.dependents):
        reasons.add("dangling_graph_edge")
    known_risk_classes = set(policy.risk_classes)
    if any(
        risk_class not in known_risk_classes for node in nodes for risk_class in node.risk_classes
    ):
        reasons.add("unknown_graph_risk_class")
    if any(validate_path_pattern(pattern) is not None for pattern in artifact_global_risk_paths):
        reasons.add("invalid_global_risk_path")
    return reasons


def _graph_context_hash(
    *,
    provenance: GraphProvenance,
    admitted_repo_epoch_hash: str,
    admitted_diff_hash: str,
    admitted_config_epoch_id: str,
    admitted_policy_hash: str,
    admitted_compiled_policy_hash: str,
    fresh: bool,
    nodes: tuple[DependencyGraphNode, ...],
    global_risk_paths: tuple[str, ...],
    invalidating_reasons: tuple[str, ...],
    unknown_path_mode: Literal["full-ci"],
) -> str:
    return hash_object(
        {
            "provenance": provenance.to_identity_mapping(),
            "admittedRepoEpochHash": admitted_repo_epoch_hash,
            "admittedDiffHash": admitted_diff_hash,
            "admittedConfigEpochId": admitted_config_epoch_id,
            "admittedPolicyHash": admitted_policy_hash,
            "admittedCompiledPolicyHash": admitted_compiled_policy_hash,
            "fresh": fresh,
            "globalRiskPaths": list(global_risk_paths),
            "nodes": [node.to_identity_mapping() for node in nodes],
            "invalidatingReasons": list(invalidating_reasons),
            "unknownPathMode": unknown_path_mode,
        }
    )


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=utf16_sort_key))
