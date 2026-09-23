"""Trusted target authority and optional capacity evidence."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from ci_coordinator.execution_orchestration import (
    ExecutionKind,
    TargetExecutionRegistry,
    TrustedExecutionTarget,
)
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context.runner_selector import StaticRunnerSelector
from ci_coordinator.runner_capacity.model import ManifestTest, ShardPlan

_CAPACITY_CLASS_ID = re.compile(r"[a-z][a-z0-9._-]{0,63}")
MAX_CAPACITY_CLASS_SELECTORS: Final = 64


@dataclass(frozen=True, slots=True)
class TrustedCapacityManifest:
    tests: tuple[ManifestTest, ...]
    duration_history: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if type(self.tests) is not tuple or any(
            type(item) is not ManifestTest for item in self.tests
        ):
            raise TypeError("capacity manifest requires an exact tuple of tests")
        test_ids = tuple(item.test_id for item in self.tests)
        if tuple(sorted(set(test_ids), key=utf16_sort_key)) != test_ids:
            raise ValueError("capacity manifest test identities must be canonical")
        if type(self.duration_history) is not tuple:
            raise TypeError("capacity history requires an exact tuple")

        history_ids: list[str] = []
        for item in self.duration_history:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("capacity history entries must be exact pairs")
            test_id, duration = item
            if type(test_id) is not str or not test_id:
                raise ValueError("capacity history identities must be non-empty strings")
            if not _positive_finite_number(duration):
                raise ValueError("capacity history durations must be positive finite numbers")
            history_ids.append(test_id)
        if tuple(sorted(set(history_ids), key=utf16_sort_key)) != tuple(history_ids):
            raise ValueError("capacity history identities must be canonical")
        if not set(history_ids).issubset(test_ids):
            raise ValueError("capacity history cannot contain tests outside the manifest")

    def history_mapping(self) -> dict[str, float]:
        return {test_id: float(duration) for test_id, duration in self.duration_history}


@dataclass(frozen=True, slots=True)
class CapacityClassSelector:
    capacity_class_id: str
    labels: tuple[str, ...]
    group: str | None

    def __post_init__(self) -> None:
        if (
            type(self.capacity_class_id) is not str
            or _CAPACITY_CLASS_ID.fullmatch(self.capacity_class_id) is None
        ):
            raise ValueError("capacity class selector requires a canonical class id")
        StaticRunnerSelector(labels=self.labels, group=self.group)

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "capacityClassId": self.capacity_class_id,
            "labels": list(self.labels),
            "group": self.group,
        }


@dataclass(frozen=True, slots=True)
class TrustedExecutionInputs:
    target_registry: TargetExecutionRegistry
    capacity_manifest: TrustedCapacityManifest | None
    capacity_selectors: tuple[CapacityClassSelector, ...] = ()

    def __post_init__(self) -> None:
        if type(self.target_registry) is not TargetExecutionRegistry:
            raise TypeError("execution inputs require an exact target execution registry")
        if (
            self.capacity_manifest is not None
            and type(self.capacity_manifest) is not TrustedCapacityManifest
        ):
            raise TypeError("capacity manifest must be exact when present")
        if type(self.capacity_selectors) is not tuple or any(
            type(item) is not CapacityClassSelector for item in self.capacity_selectors
        ):
            raise TypeError("capacity selectors must be an exact tuple")
        if len(self.capacity_selectors) > MAX_CAPACITY_CLASS_SELECTORS:
            raise ValueError("capacity selectors exceed the admitted maximum")
        class_ids = tuple(item.capacity_class_id for item in self.capacity_selectors)
        if tuple(sorted(set(class_ids), key=utf16_sort_key)) != class_ids:
            raise ValueError("capacity selectors must be canonical by class")
        sharded_capacity_classes = {
            profile.capacity_class_id
            for profile in self.target_registry.profiles
            if profile.execution_kind == "witness-shards"
        }
        if not set(class_ids).issubset(sharded_capacity_classes):
            raise ValueError("capacity selectors must belong to sharded target profiles")


@dataclass(frozen=True, slots=True)
class TrustedExecutionProjection:
    target: TrustedExecutionTarget
    shard_plan: ShardPlan | None

    def __post_init__(self) -> None:
        if type(self.target) is not TrustedExecutionTarget:
            raise TypeError("execution projection requires an exact target")
        if self.execution_kind == "native-job-set":
            if self.shard_plan is not None:
                raise ValueError("native execution projection cannot contain a shard plan")
            return
        if self.execution_kind != "witness-shards" or type(self.shard_plan) is not ShardPlan:
            raise ValueError("sharded execution projection requires an exact shard plan")
        if (
            self.shard_plan.verified_plan_id != self.verified_plan_id
            or self.shard_plan.manifest.target_registry_hash != self.target_registry.registry_hash
            or tuple(item.profile_id for item in self.shard_plan.profile_plans)
            != self.selected_profile_ids
        ):
            raise ValueError("shard plan does not bind the exact execution projection")

    @property
    def verified_plan_id(self) -> str:
        return self.target.verified_plan_id

    @property
    def workflow_path(self) -> str:
        return self.target.workflow_path

    @property
    def execution_kind(self) -> ExecutionKind:
        return self.target.execution_kind

    @property
    def selected_profile_ids(self) -> tuple[str, ...]:
        return self.target.selected_profile_ids

    @property
    def target_registry(self) -> TargetExecutionRegistry:
        return self.target.target_registry

    @property
    def target_registry_hash(self) -> str:
        return self.target.target_registry_hash


def _positive_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )
