"""Pure eligibility projection for observed self-hosted runner evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context.runner_selector import MAX_RUNNER_SELECTOR_TEXT_BYTES
from ci_coordinator.runner_capacity.inputs import CapacityClassSelector
from ci_coordinator.runner_capacity.model import RunnerCapacity, RunnerSnapshot

MAX_OBSERVED_RUNNER_LABELS: Final = 64


@dataclass(frozen=True, slots=True)
class ObservedSelfHostedRunner:
    runner_id: int
    online: bool
    busy: bool
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.runner_id) is not int or self.runner_id < 1:
            raise ValueError("observed runner id must be positive")
        if type(self.online) is not bool or type(self.busy) is not bool:
            raise TypeError("observed runner state must use exact booleans")
        if type(self.labels) is not tuple or len(self.labels) > MAX_OBSERVED_RUNNER_LABELS:
            raise TypeError("observed runner labels must be a bounded exact tuple")
        if any(not _bounded_ascii_label(label) or label != label.lower() for label in self.labels):
            raise ValueError("observed runner labels must be normalized bounded ASCII text")
        if tuple(sorted(set(self.labels), key=utf16_sort_key)) != self.labels:
            raise ValueError("observed runner labels must be canonical")


@dataclass(frozen=True, slots=True)
class VisibleRunnerGroup:
    group_id: int
    name: str
    restricted_to_workflows: bool
    runners: tuple[ObservedSelfHostedRunner, ...]

    def __post_init__(self) -> None:
        if type(self.group_id) is not int or self.group_id < 1:
            raise ValueError("visible runner group id must be positive")
        if not _bounded_text(self.name):
            raise ValueError("visible runner group name must be bounded text")
        if type(self.restricted_to_workflows) is not bool:
            raise TypeError("runner group restriction must be an exact boolean")
        if type(self.runners) is not tuple or any(
            type(runner) is not ObservedSelfHostedRunner for runner in self.runners
        ):
            raise TypeError("runner group members must be an exact tuple")
        runner_ids = tuple(runner.runner_id for runner in self.runners)
        if tuple(sorted(set(runner_ids))) != runner_ids:
            raise ValueError("runner group members must be canonical")


def project_runner_snapshot(
    *,
    selectors: tuple[CapacityClassSelector, ...],
    repository_runners: tuple[ObservedSelfHostedRunner, ...],
    groups: tuple[VisibleRunnerGroup, ...],
    observed_at: datetime,
    freshness_ttl_seconds: int,
) -> RunnerSnapshot | None:
    """Project only disjoint, fully identified eligible pools into capacity."""

    if type(selectors) is not tuple or any(
        type(item) is not CapacityClassSelector for item in selectors
    ):
        raise TypeError("runner snapshot projection requires exact capacity selectors")
    if type(repository_runners) is not tuple or any(
        type(item) is not ObservedSelfHostedRunner for item in repository_runners
    ):
        raise TypeError("runner snapshot projection requires exact repository runners")
    if type(groups) is not tuple or any(type(item) is not VisibleRunnerGroup for item in groups):
        raise TypeError("runner snapshot projection requires exact visible groups")
    if tuple(group.group_id for group in groups) != tuple(
        sorted({group.group_id for group in groups})
    ):
        raise ValueError("visible runner groups must be canonical")

    runners = _consistent_runner_index(repository_runners, groups)
    membership = _unique_group_membership(groups)
    if runners is None or membership is None:
        return None

    groups_by_name: dict[str, list[VisibleRunnerGroup]] = {}
    for group in groups:
        groups_by_name.setdefault(group.name, []).append(group)
    runner_labels = {runner_id: frozenset(runner.labels) for runner_id, runner in runners.items()}

    pools: dict[str, frozenset[int]] = {}
    for selector in selectors:
        candidate_ids = _candidate_runner_ids(
            selector,
            repository_runners=repository_runners,
            groups_by_name=groups_by_name,
            membership=membership,
        )
        if candidate_ids is None:
            continue
        required_labels = frozenset(selector.labels)
        pools[selector.capacity_class_id] = frozenset(
            runner_id
            for runner_id in candidate_ids
            if required_labels.issubset(runner_labels[runner_id])
        )

    intersecting = {
        left
        for left, left_pool in pools.items()
        for right, right_pool in pools.items()
        if left != right and not left_pool.isdisjoint(right_pool)
    }
    capacities = tuple(
        RunnerCapacity(
            capacity_class_id=capacity_class_id,
            free_slots=sum(
                runners[runner_id].online and not runners[runner_id].busy
                for runner_id in pools[capacity_class_id]
            ),
        )
        for capacity_class_id in sorted(pools, key=utf16_sort_key)
        if capacity_class_id not in intersecting
    )
    return RunnerSnapshot(
        observed_at=observed_at,
        freshness_ttl_seconds=freshness_ttl_seconds,
        capacities=capacities,
        source="github-self-hosted",
    )


def _consistent_runner_index(
    repository_runners: tuple[ObservedSelfHostedRunner, ...],
    groups: tuple[VisibleRunnerGroup, ...],
) -> dict[int, ObservedSelfHostedRunner] | None:
    index: dict[int, ObservedSelfHostedRunner] = {}
    observed = (*repository_runners, *(runner for group in groups for runner in group.runners))
    for runner in observed:
        previous = index.get(runner.runner_id)
        if previous is None:
            index[runner.runner_id] = runner
            continue
        if previous.labels != runner.labels:
            return None
        index[runner.runner_id] = ObservedSelfHostedRunner(
            runner_id=runner.runner_id,
            online=previous.online and runner.online,
            busy=previous.busy or runner.busy,
            labels=runner.labels,
        )
    return index


def _unique_group_membership(
    groups: tuple[VisibleRunnerGroup, ...],
) -> dict[int, VisibleRunnerGroup] | None:
    membership: dict[int, VisibleRunnerGroup] = {}
    for group in groups:
        for runner in group.runners:
            if runner.runner_id in membership:
                return None
            membership[runner.runner_id] = group
    return membership


def _candidate_runner_ids(
    selector: CapacityClassSelector,
    *,
    repository_runners: tuple[ObservedSelfHostedRunner, ...],
    groups_by_name: dict[str, list[VisibleRunnerGroup]],
    membership: dict[int, VisibleRunnerGroup],
) -> set[int] | None:
    if selector.group is not None:
        matching_groups = groups_by_name.get(selector.group, [])
        if len(matching_groups) > 1 or (
            matching_groups and matching_groups[0].restricted_to_workflows
        ):
            return None
        return (
            set()
            if not matching_groups
            else {runner.runner_id for runner in matching_groups[0].runners}
        )

    candidates = {
        runner.runner_id
        for runner in repository_runners
        if not (
            runner.runner_id in membership and membership[runner.runner_id].restricted_to_workflows
        )
    }
    candidates.update(
        runner.runner_id
        for group_list in groups_by_name.values()
        for group in group_list
        if not group.restricted_to_workflows
        for runner in group.runners
    )
    return candidates


def _bounded_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not any(
            ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
        and len(value.encode("utf-8")) <= MAX_RUNNER_SELECTOR_TEXT_BYTES
    )


def _bounded_ascii_label(value: object) -> bool:
    if type(value) is not str:
        return False
    return value.isascii() and _bounded_text(value)
