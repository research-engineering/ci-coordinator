"""Bind target capacity classes to exact-revision static runner selectors."""

from __future__ import annotations

from ci_coordinator.execution_orchestration import TargetExecutionRegistry
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context import ProviderWorkflowInventory, StaticRunnerSelector
from ci_coordinator.runner_capacity.inputs import CapacityClassSelector


def bind_capacity_class_selectors(
    registry: TargetExecutionRegistry,
    inventory: ProviderWorkflowInventory,
) -> tuple[CapacityClassSelector, ...]:
    """Return only classes whose every sharded profile proves one identical selector."""

    if type(registry) is not TargetExecutionRegistry:
        raise TypeError("capacity selector binding requires an exact target registry")
    if type(inventory) is not ProviderWorkflowInventory:
        raise TypeError("capacity selector binding requires an exact workflow inventory")

    capabilities = {item.path: item for item in inventory.revision_capabilities}
    selectors_by_class: dict[str, set[StaticRunnerSelector]] = {}
    unresolved_classes: set[str] = set()
    for profile in registry.profiles:
        if profile.execution_kind != "witness-shards":
            continue
        capability = capabilities.get(profile.workflow_path)
        selector = None if capability is None else capability.runner_selector(profile.job_id)
        if selector is None:
            unresolved_classes.add(profile.capacity_class_id)
            continue
        selectors_by_class.setdefault(profile.capacity_class_id, set()).add(selector)

    admitted: list[CapacityClassSelector] = []
    for capacity_class_id in sorted(selectors_by_class, key=utf16_sort_key):
        selectors = selectors_by_class[capacity_class_id]
        if capacity_class_id in unresolved_classes or len(selectors) != 1:
            continue
        selector = next(iter(selectors))
        admitted.append(
            CapacityClassSelector(
                capacity_class_id=capacity_class_id,
                labels=selector.labels,
                group=selector.group,
            )
        )
    return tuple(admitted)
