"""Canonical, fallback-aware repository facts for deterministic planning."""

from ci_coordinator.repo_context.dependency_graph import (
    DependencyGraphArtifact,
    DependencyGraphContext,
    DependencyGraphNode,
    GraphProvenance,
    build_dependency_graph,
)
from ci_coordinator.repo_context.dependency_graph_codec import (
    DEPENDENCY_GRAPH_SCHEMA_VERSION,
    MAX_DEPENDENCY_GRAPH_BYTES,
    MAX_DEPENDENCY_GRAPH_NODES,
    parse_dependency_graph_artifact,
)
from ci_coordinator.repo_context.diff_builder import (
    DiffBuildInput,
    DiffFileChangeInput,
    build_diff_context,
)
from ci_coordinator.repo_context.diff_model import (
    DiffContext,
    DiffSource,
    FileDelta,
    RepositoryEpoch,
)
from ci_coordinator.repo_context.freshness import matches_path_pattern
from ci_coordinator.repo_context.planning_input import (
    PlanningInput,
    PolicySnapshot,
    build_planning_input,
)
from ci_coordinator.repo_context.runner_selector import (
    MAX_RUNNER_SELECTOR_LABELS,
    MAX_RUNNER_SELECTOR_TEXT_BYTES,
    StaticRunnerSelector,
    WorkflowJobRunnerSelector,
)
from ci_coordinator.repo_context.workflow_inventory import (
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    ProviderWorkflowInventoryEvidence,
    RevisionWorkflowCapability,
    is_git_object_revision,
    is_workflow_path,
)
from ci_coordinator.repo_context.workflow_syntax import (
    MAX_WORKFLOW_FILE_BYTES,
    parse_workflow_capability,
)

__all__ = [
    "DEPENDENCY_GRAPH_SCHEMA_VERSION",
    "MAX_DEPENDENCY_GRAPH_BYTES",
    "MAX_DEPENDENCY_GRAPH_NODES",
    "MAX_RUNNER_SELECTOR_LABELS",
    "MAX_RUNNER_SELECTOR_TEXT_BYTES",
    "MAX_WORKFLOW_FILE_BYTES",
    "DefaultBranchWorkflow",
    "DependencyGraphArtifact",
    "DependencyGraphContext",
    "DependencyGraphNode",
    "DiffBuildInput",
    "DiffContext",
    "DiffFileChangeInput",
    "DiffSource",
    "FileDelta",
    "GraphProvenance",
    "PlanningInput",
    "PolicySnapshot",
    "ProviderWorkflowInventory",
    "ProviderWorkflowInventoryEvidence",
    "RepositoryEpoch",
    "RevisionWorkflowCapability",
    "StaticRunnerSelector",
    "WorkflowJobRunnerSelector",
    "build_dependency_graph",
    "build_diff_context",
    "build_planning_input",
    "is_git_object_revision",
    "is_workflow_path",
    "matches_path_pattern",
    "parse_dependency_graph_artifact",
    "parse_workflow_capability",
]
