from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.repo_context.freshness import matches_path_pattern


@dataclass(frozen=True, slots=True)
class ImpactAnalysis:
    changed_paths: tuple[str, ...]
    impacted_paths: tuple[str, ...]
    impacted_risk_classes: tuple[str, ...]
    unknown_paths: tuple[str, ...]
    global_risk_paths: tuple[str, ...]


def analyze_impact(input: PlanningInput) -> ImpactAnalysis:
    changed_paths = input.diff.changed_paths
    node_by_path = {node.path: node for node in input.dependency_graph.nodes}
    impacted_paths = set(changed_paths)
    impacted_risk_classes: set[str] = set()
    unknown_paths = {path for path in changed_paths if path not in node_by_path}
    global_risk_paths = {
        path
        for path in changed_paths
        if any(
            matches_path_pattern(pattern, path)
            for pattern in input.dependency_graph.global_risk_paths
        )
    }
    queue = list(changed_paths)
    index = 0
    while index < len(queue):
        path = queue[index]
        index += 1
        node = node_by_path.get(path)
        if node is None:
            unknown_paths.add(path)
            continue
        impacted_risk_classes.update(node.risk_classes)
        for dependent in node.dependents:
            if dependent not in impacted_paths:
                impacted_paths.add(dependent)
                queue.append(dependent)

    return ImpactAnalysis(
        changed_paths=changed_paths,
        impacted_paths=tuple(sorted(impacted_paths, key=utf16_sort_key)),
        impacted_risk_classes=tuple(sorted(impacted_risk_classes, key=utf16_sort_key)),
        unknown_paths=tuple(sorted(unknown_paths, key=utf16_sort_key)),
        global_risk_paths=tuple(sorted(global_risk_paths, key=utf16_sort_key)),
    )
