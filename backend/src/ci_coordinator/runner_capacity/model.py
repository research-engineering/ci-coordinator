"""Immutable capacity observations, test manifests, and profile shard plans."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.validation_contract import (
    ExecutionProfile,
    ShardingPolicy,
    ValidationCatalog,
)

MAX_GITHUB_MATRIX_JOBS = 256


@dataclass(frozen=True, slots=True)
class ManifestTest:
    test_id: str
    witness_id: str

    def __post_init__(self) -> None:
        _require_nonempty_string(self.test_id, field_name="test_id")
        _require_nonempty_string(self.witness_id, field_name="witness_id")

    def to_identity_mapping(self) -> dict[str, str]:
        return {"testId": self.test_id, "witnessId": self.witness_id}


@dataclass(frozen=True, slots=True)
class TestManifest:
    verified_plan_id: str
    target_registry_hash: str
    selected_witness_ids: tuple[str, ...]
    tests: tuple[ManifestTest, ...]
    catalog: ValidationCatalog

    def __post_init__(self) -> None:
        _require_nonempty_string(self.verified_plan_id, field_name="verified_plan_id")
        if (
            type(self.target_registry_hash) is not str
            or len(self.target_registry_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.target_registry_hash)
        ):
            raise ValueError("target_registry_hash must be lowercase SHA-256 hexadecimal")
        _require_canonical_strings(
            self.selected_witness_ids,
            field_name="selected_witness_ids",
        )
        if type(self.tests) is not tuple or any(
            type(item) is not ManifestTest for item in self.tests
        ):
            raise TypeError("manifest tests must be an exact tuple of ManifestTest values")
        test_ids = tuple(item.test_id for item in self.tests)
        if tuple(sorted(set(test_ids), key=utf16_sort_key)) != test_ids:
            raise ValueError("manifest test identities must be canonical")
        if type(self.catalog) is not ValidationCatalog:
            raise TypeError("test manifest requires an exact ValidationCatalog")

        witness_by_id = {item.witness_id: item for item in self.catalog.witnesses}
        selected = set(self.selected_witness_ids)
        if not selected.issubset(witness_by_id):
            raise ValueError("selected witnesses must resolve in the validation catalog")
        if {item.witness_id for item in self.tests} != selected:
            raise ValueError("manifest tests must cover every selected witness")

    @property
    def manifest_id(self) -> str:
        return "test_manifest_" + hash_object(self.to_identity_mapping())[:32]

    @property
    def execution_profiles(self) -> tuple[ExecutionProfile, ...]:
        witness_by_id = {item.witness_id: item for item in self.catalog.witnesses}
        profile_by_id = {item.profile_id: item for item in self.catalog.execution_profiles}
        profile_ids = {
            witness_by_id[witness_id].execution_profile_id
            for witness_id in self.selected_witness_ids
        }
        return tuple(
            profile_by_id[profile_id] for profile_id in sorted(profile_ids, key=utf16_sort_key)
        )

    def profile_id_for_test(self, test_id: str) -> str:
        test_by_id = {item.test_id: item for item in self.tests}
        try:
            witness_id = test_by_id[test_id].witness_id
        except KeyError as error:
            raise ValueError("test identity is not present in the manifest") from error
        return self._profile_by_witness_id()[witness_id]

    def tests_for_profile(self, profile_id: str) -> tuple[ManifestTest, ...]:
        profile_by_witness_id = self._profile_by_witness_id()
        return tuple(
            item for item in self.tests if profile_by_witness_id[item.witness_id] == profile_id
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "verifiedPlanId": self.verified_plan_id,
            "targetRegistryHash": self.target_registry_hash,
            "catalogHash": self.catalog.catalog_hash,
            "selectedWitnessIds": list(self.selected_witness_ids),
            "tests": [item.to_identity_mapping() for item in self.tests],
        }

    def _profile_by_witness_id(self) -> dict[str, str]:
        return {item.witness_id: item.execution_profile_id for item in self.catalog.witnesses}


@dataclass(frozen=True, slots=True)
class RunnerCapacity:
    capacity_class_id: str
    free_slots: int

    def __post_init__(self) -> None:
        _require_nonempty_string(self.capacity_class_id, field_name="capacity_class_id")
        if type(self.free_slots) is not int or self.free_slots < 0:
            raise ValueError("free_slots must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class RunnerSnapshot:
    observed_at: datetime
    freshness_ttl_seconds: int
    capacities: tuple[RunnerCapacity, ...]
    source: Literal["github-self-hosted", "operator"]

    def __post_init__(self) -> None:
        if (
            type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("snapshot observation time must be timezone-aware")
        if type(self.freshness_ttl_seconds) is not int or self.freshness_ttl_seconds < 1:
            raise ValueError("snapshot freshness ttl must be a positive integer")
        if type(self.capacities) is not tuple or any(
            type(item) is not RunnerCapacity for item in self.capacities
        ):
            raise TypeError("snapshot capacities must be an exact tuple of RunnerCapacity values")
        capacity_classes = tuple(item.capacity_class_id for item in self.capacities)
        if tuple(sorted(set(capacity_classes), key=utf16_sort_key)) != capacity_classes:
            raise ValueError("snapshot capacity classes must be canonical")
        if self.source not in {"github-self-hosted", "operator"}:
            raise ValueError("snapshot source is not admitted")

    def is_fresh(self, now: datetime) -> bool:
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            return False
        try:
            return (
                self.observed_at
                <= now
                <= self.observed_at + timedelta(seconds=self.freshness_ttl_seconds)
            )
        except (OverflowError, TypeError, ValueError):
            return False

    def free_slots_for(self, capacity_class_id: str) -> int | None:
        return next(
            (
                item.free_slots
                for item in self.capacities
                if item.capacity_class_id == capacity_class_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class Shard:
    manifest_id: str
    profile_id: str
    test_ids: tuple[str, ...]
    expected_seconds: float

    def __post_init__(self) -> None:
        _require_nonempty_string(self.manifest_id, field_name="manifest_id")
        _require_nonempty_string(self.profile_id, field_name="profile_id")
        _require_canonical_strings(self.test_ids, field_name="shard test identities")
        if not _nonnegative_finite_number(self.expected_seconds):
            raise ValueError("shard expected seconds must be a non-negative finite number")

    @property
    def shard_id(self) -> str:
        return (
            "ci_shard_"
            + hash_object(
                {
                    "manifestId": self.manifest_id,
                    "profileId": self.profile_id,
                    "testIds": list(self.test_ids),
                }
            )[:32]
        )


@dataclass(frozen=True, slots=True)
class ProfileShardPlan:
    profile_id: str
    sharding_policy: ShardingPolicy
    shards: tuple[Shard, ...]
    max_parallel: int
    mode: Literal["optimized", "conservative"]
    reason: str | None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.profile_id, field_name="profile_id")
        if type(self.sharding_policy) is not ShardingPolicy:
            raise TypeError("profile shard plan requires an exact ShardingPolicy")
        if type(self.shards) is not tuple or any(type(item) is not Shard for item in self.shards):
            raise TypeError("profile shards must be an exact tuple of Shard values")
        if not self.shards:
            raise ValueError("profile shard plan must contain at least one shard")
        if any(item.profile_id != self.profile_id for item in self.shards):
            raise ValueError("a profile shard plan cannot mix execution profiles")
        shard_ids = tuple(item.shard_id for item in self.shards)
        if tuple(sorted(set(shard_ids), key=utf16_sort_key)) != shard_ids:
            raise ValueError("profile shard identities must be canonical")
        if len(self.shards) > min(self.sharding_policy.max_shards, MAX_GITHUB_MATRIX_JOBS):
            raise ValueError("profile shard count exceeds the admitted matrix bound")
        if any(
            len(item.test_ids) > self.sharding_policy.max_items_per_shard for item in self.shards
        ):
            raise ValueError("profile shard exceeds max_items_per_shard")
        if (
            type(self.max_parallel) is not int
            or not 1 <= self.max_parallel <= len(self.shards)
            or self.max_parallel > self.sharding_policy.max_parallel
        ):
            raise ValueError("profile max_parallel exceeds the admitted policy")
        if self.mode not in {"optimized", "conservative"}:
            raise ValueError("profile shard plan mode is not admitted")
        if self.mode == "optimized":
            if self.reason is not None:
                raise ValueError("optimized profile plans have no fallback reason")
        elif type(self.reason) is not str or not self.reason:
            raise ValueError("conservative profile plans require a reason")


@dataclass(frozen=True, slots=True)
class ShardPlan:
    manifest: TestManifest
    profile_plans: tuple[ProfileShardPlan, ...]

    def __post_init__(self) -> None:
        if type(self.manifest) is not TestManifest:
            raise TypeError("shard plan requires an exact TestManifest")
        if type(self.profile_plans) is not tuple or any(
            type(item) is not ProfileShardPlan for item in self.profile_plans
        ):
            raise TypeError("profile plans must be an exact tuple of ProfileShardPlan values")
        profile_ids = tuple(item.profile_id for item in self.profile_plans)
        if tuple(sorted(set(profile_ids), key=utf16_sort_key)) != profile_ids:
            raise ValueError("profile shard plans must be canonical")

        expected_profiles = {item.profile_id: item for item in self.manifest.execution_profiles}
        if set(profile_ids) != set(expected_profiles):
            raise ValueError("shard plan must cover every selected execution profile")
        for plan in self.profile_plans:
            if plan.sharding_policy != expected_profiles[plan.profile_id].sharding_policy:
                raise ValueError("profile shard plan must use its catalog sharding policy")
            if any(shard.manifest_id != self.manifest.manifest_id for shard in plan.shards):
                raise ValueError("shard identity must bind the exact test manifest")

        assigned_test_ids = tuple(
            test_id
            for profile_plan in self.profile_plans
            for shard in profile_plan.shards
            for test_id in shard.test_ids
        )
        manifest_test_ids = tuple(item.test_id for item in self.manifest.tests)
        if len(set(assigned_test_ids)) != len(assigned_test_ids) or set(assigned_test_ids) != set(
            manifest_test_ids
        ):
            raise ValueError("shard plan must cover every manifest test exactly once")
        profile_by_witness_id = {
            item.witness_id: item.execution_profile_id for item in self.manifest.catalog.witnesses
        }
        profile_by_test_id = {
            item.test_id: profile_by_witness_id[item.witness_id] for item in self.manifest.tests
        }
        for profile_plan in self.profile_plans:
            if any(
                profile_by_test_id[test_id] != profile_plan.profile_id
                for shard in profile_plan.shards
                for test_id in shard.test_ids
            ):
                raise ValueError("a shard cannot contain a test from another execution profile")

    @property
    def manifest_id(self) -> str:
        return self.manifest.manifest_id

    @property
    def verified_plan_id(self) -> str:
        return self.manifest.verified_plan_id


def _require_nonempty_string(value: object, *, field_name: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_canonical_strings(values: tuple[str, ...], *, field_name: str) -> None:
    if (
        type(values) is not tuple
        or not values
        or any(type(item) is not str or not item for item in values)
    ):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be canonical")


def _nonnegative_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0
    )
