from __future__ import annotations

from package_b_support import make_input, make_policy

from ci_coordinator.planning_core import PlanningPolicy
from ci_coordinator.repo_context import DependencyGraphNode, DiffFileChangeInput, PlanningInput
from ci_coordinator.validation_contract import ValidationCatalog


def make_policy_value(
    *,
    policy_hash: str = "d" * 64,
    catalog: ValidationCatalog | None = None,
) -> PlanningPolicy:
    return make_policy(policy_hash=policy_hash, catalog=catalog)


def make_input_value(
    *files: DiffFileChangeInput,
    graph_nodes: tuple[DependencyGraphNode, ...] | None = None,
    global_risk_paths: tuple[str, ...] = (".github/workflows/**", "ci/**"),
    graph_retrieved_for_sha: str = "b" * 40,
) -> PlanningInput:
    return make_input(
        *files,
        graph_nodes=graph_nodes,
        global_risk_paths=global_risk_paths,
        graph_retrieved_for_sha=graph_retrieved_for_sha,
    )
