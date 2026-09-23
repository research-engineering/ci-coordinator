"""Deterministic profile-local LPT shard optimization."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from heapq import heapify, heappop, heappush

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.runner_capacity.model import (
    ManifestTest,
    ProfileShardPlan,
    RunnerSnapshot,
    Shard,
    ShardPlan,
    TestManifest,
)
from ci_coordinator.validation_contract import ExecutionProfile, ShardingPolicy


def optimize_shards(
    manifest: TestManifest,
    snapshot: RunnerSnapshot | None,
    history: Mapping[str, float],
    *,
    now: datetime,
) -> ShardPlan:
    if type(manifest) is not TestManifest:
        raise TypeError("capacity optimization requires an exact TestManifest")
    if snapshot is not None and type(snapshot) is not RunnerSnapshot:
        raise TypeError("capacity snapshot must be an exact RunnerSnapshot or None")

    if snapshot is None:
        return _conservative_plan(manifest, "runner_capacity_unknown", history)
    if not snapshot.is_fresh(now):
        return _conservative_plan(manifest, "runner_capacity_stale", history)

    capacity_budgets = _profile_capacity_budgets(manifest, snapshot)
    profile_plans = tuple(
        _optimize_profile(
            manifest,
            profile,
            capacity_budgets[profile.profile_id],
            history,
            zero_capacity_reason=_zero_capacity_reason(
                snapshot,
                profile,
                capacity_budgets[profile.profile_id],
            ),
        )
        for profile in manifest.execution_profiles
    )
    return ShardPlan(manifest=manifest, profile_plans=profile_plans)


def _profile_capacity_budgets(
    manifest: TestManifest,
    snapshot: RunnerSnapshot,
) -> dict[str, int | None]:
    by_capacity_class: dict[str, list[ExecutionProfile]] = {}
    for profile in manifest.execution_profiles:
        by_capacity_class.setdefault(profile.capacity_class_id, []).append(profile)

    budgets: dict[str, int | None] = {}
    for capacity_class_id, profiles in by_capacity_class.items():
        free_slots = snapshot.free_slots_for(capacity_class_id)
        if free_slots is None:
            budgets.update((profile.profile_id, None) for profile in profiles)
            continue
        allocated = {profile.profile_id: 0 for profile in profiles}
        remaining = free_slots
        for profile in profiles:
            if remaining == 0:
                break
            allocated[profile.profile_id] = 1
            remaining -= 1
        while remaining:
            eligible = tuple(
                profile
                for profile in profiles
                if allocated[profile.profile_id] < _profile_parallel_demand(manifest, profile)
            )
            if not eligible:
                break
            for profile in eligible:
                if remaining == 0:
                    break
                allocated[profile.profile_id] += 1
                remaining -= 1
        budgets.update(allocated)
    return budgets


def _profile_parallel_demand(manifest: TestManifest, profile: ExecutionProfile) -> int:
    return min(
        len(manifest.tests_for_profile(profile.profile_id)),
        profile.sharding_policy.max_shards,
        profile.sharding_policy.max_parallel,
    )


def _zero_capacity_reason(
    snapshot: RunnerSnapshot,
    profile: ExecutionProfile,
    allocated_slots: int | None,
) -> str:
    pool_slots = snapshot.free_slots_for(profile.capacity_class_id)
    return (
        "runner_capacity_shared_pool_exhausted"
        if allocated_slots == 0 and pool_slots is not None and pool_slots > 0
        else "runner_capacity_unavailable"
    )


def _optimize_profile(
    manifest: TestManifest,
    profile: ExecutionProfile,
    free_slots: int | None,
    history: Mapping[str, float],
    *,
    zero_capacity_reason: str,
) -> ProfileShardPlan:
    tests = manifest.tests_for_profile(profile.profile_id)
    minimum_shards = _minimum_shards(profile.sharding_policy, len(tests))
    if free_slots is None:
        return _conservative_profile(
            manifest.manifest_id,
            profile,
            tests,
            "runner_capacity_unknown",
            history,
        )
    if free_slots < 1:
        return _conservative_profile(
            manifest.manifest_id,
            profile,
            tests,
            zero_capacity_reason,
            history,
        )

    durations = _durations(tests, history)
    if durations is None:
        return _conservative_profile(
            manifest.manifest_id,
            profile,
            tests,
            "test_duration_history_missing",
            history,
        )
    policy = profile.sharding_policy
    ordered_tests = tuple(
        sorted(
            tests,
            key=lambda item: (-durations[item.test_id], utf16_sort_key(item.test_id)),
        )
    )
    maximum_shards = max(
        minimum_shards,
        min(
            policy.max_shards,
            free_slots,
            len(tests),
        ),
    )
    return min(
        (
            _candidate(
                manifest.manifest_id,
                profile,
                ordered_tests,
                durations,
                count,
                free_slots=free_slots,
            )
            for count in range(minimum_shards, maximum_shards + 1)
        ),
        key=lambda item: (
            _cost(item),
            len(item.shards),
            tuple(shard.shard_id for shard in item.shards),
        ),
    )


def _durations(
    tests: tuple[ManifestTest, ...], history: Mapping[str, float]
) -> dict[str, float] | None:
    result: dict[str, float] = {}
    for test in tests:
        duration = _positive_duration(history.get(test.test_id))
        if duration is None:
            return None
        result[test.test_id] = duration
    return result


def _candidate(
    manifest_id: str,
    profile: ExecutionProfile,
    tests: tuple[ManifestTest, ...],
    durations: Mapping[str, float],
    count: int,
    *,
    free_slots: int,
) -> ProfileShardPlan:
    buckets: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0 for _ in range(count)]
    eligible = [(0.0, index) for index in range(count)]
    heapify(eligible)
    for test in tests:
        _, index = heappop(eligible)
        buckets[index].append(test.test_id)
        totals[index] += durations[test.test_id]
        if len(buckets[index]) < profile.sharding_policy.max_items_per_shard:
            heappush(eligible, (totals[index], index))

    policy = profile.sharding_policy
    shards = tuple(
        sorted(
            (
                Shard(
                    manifest_id=manifest_id,
                    profile_id=profile.profile_id,
                    test_ids=tuple(sorted(bucket, key=utf16_sort_key)),
                    expected_seconds=totals[index] + policy.setup_seconds_per_shard,
                )
                for index, bucket in enumerate(buckets)
            ),
            key=lambda item: utf16_sort_key(item.shard_id),
        )
    )
    return ProfileShardPlan(
        profile_id=profile.profile_id,
        sharding_policy=policy,
        shards=shards,
        max_parallel=min(count, policy.max_parallel, free_slots),
        mode="optimized",
        reason=None,
    )


def _cost(plan: ProfileShardPlan) -> float:
    policy = plan.sharding_policy
    cpu = sum(item.expected_seconds for item in plan.shards)
    wall = _parallel_makespan(plan.shards, plan.max_parallel)
    return (
        policy.cpu_weight * cpu
        + policy.wall_weight * wall
        + policy.operator_weight * len(plan.shards)
    )


def _conservative_plan(
    manifest: TestManifest,
    reason: str,
    history: Mapping[str, float],
) -> ShardPlan:
    profile_plans: list[ProfileShardPlan] = []
    for profile in manifest.execution_profiles:
        tests = manifest.tests_for_profile(profile.profile_id)
        _minimum_shards(profile.sharding_policy, len(tests))
        profile_plans.append(
            _conservative_profile(
                manifest.manifest_id,
                profile,
                tests,
                reason,
                history,
            )
        )
    return ShardPlan(
        manifest=manifest,
        profile_plans=tuple(profile_plans),
    )


def _conservative_profile(
    manifest_id: str,
    profile: ExecutionProfile,
    tests: tuple[ManifestTest, ...],
    reason: str,
    history: Mapping[str, float],
) -> ProfileShardPlan:
    buckets = tuple(
        tuple(tests[index : index + profile.sharding_policy.max_items_per_shard])
        for index in range(0, len(tests), profile.sharding_policy.max_items_per_shard)
    )
    shards = tuple(
        sorted(
            (
                Shard(
                    manifest_id,
                    profile.profile_id,
                    tuple(item.test_id for item in bucket),
                    profile.sharding_policy.setup_seconds_per_shard
                    + sum(_safe_duration(history.get(item.test_id)) for item in bucket),
                )
                for bucket in buckets
            ),
            key=lambda item: utf16_sort_key(item.shard_id),
        )
    )
    return ProfileShardPlan(
        profile_id=profile.profile_id,
        sharding_policy=profile.sharding_policy,
        shards=shards,
        max_parallel=1,
        mode="conservative",
        reason=reason,
    )


def _minimum_shards(policy: ShardingPolicy, test_count: int) -> int:
    required = (test_count + policy.max_items_per_shard - 1) // policy.max_items_per_shard
    if required > policy.max_shards:
        raise ValueError("profile test coverage exceeds the sharding policy bounds")
    return required


def _parallel_makespan(shards: tuple[Shard, ...], max_parallel: int) -> float:
    lanes = [(0.0, index) for index in range(max_parallel)]
    heapify(lanes)
    for shard in sorted(
        shards,
        key=lambda item: (-item.expected_seconds, utf16_sort_key(item.shard_id)),
    ):
        elapsed, index = heappop(lanes)
        heappush(lanes, (elapsed + shard.expected_seconds, index))
    return max(elapsed for elapsed, _ in lanes)


def _positive_duration(value: object) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        return None
    return float(value)


def _safe_duration(value: object) -> float:
    duration = _positive_duration(value)
    return 0.0 if duration is None else duration
