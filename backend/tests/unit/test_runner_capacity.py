from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from package_b_support import PLAN_REQUEST_JOB_ID, PLAN_REQUEST_WORKFLOW_REF

from ci_coordinator.execution_orchestration import (
    TARGET_CONTROL_FILE_PATHS,
    TargetAdapterFileBinding,
    TargetExecutionRegistry,
    TargetJobBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
)
from ci_coordinator.runner_capacity import (
    ManifestTest,
    ProfileShardPlan,
    RunnerCapacity,
    RunnerSnapshot,
    Shard,
    ShardingPolicy,
    ShardPlan,
    TrustedCapacityManifest,
    build_test_manifest,
    optimize_shards,
)
from ci_coordinator.runner_capacity import (
    TestManifest as _TestManifest,
)
from ci_coordinator.validation_contract import (
    ExecutableWitness,
    ExecutionProfile,
    ValidationCatalog,
    ValidationObligation,
)

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def _policy(
    *,
    max_shards: int = 4,
    max_parallel: int = 3,
    max_items_per_shard: int = 10,
) -> ShardingPolicy:
    return ShardingPolicy(
        max_shards=max_shards,
        max_parallel=max_parallel,
        max_items_per_shard=max_items_per_shard,
        setup_seconds_per_shard=1.0,
        cpu_weight=0.0,
        wall_weight=1.0,
        operator_weight=0.0,
    )


def _profile(
    profile_id: str,
    capacity_class_id: str,
    policy: ShardingPolicy,
) -> ExecutionProfile:
    return ExecutionProfile(
        profile_id=profile_id,
        runner_profile_id=profile_id + "-runner",
        permission_profile_id="read-only",
        credential_profile_id="none",
        fixture_profile_id="none",
        service_profile_ids=(),
        capacity_class_id=capacity_class_id,
        sharding_policy=policy,
    )


def _catalog(
    *,
    python_policy: ShardingPolicy | None = None,
    container_policy: ShardingPolicy | None = None,
) -> ValidationCatalog:
    witnesses = (
        ExecutableWitness("api-tests", "python", ("targeted", "full")),
        ExecutableWitness("container-smoke", "container", ("targeted", "full")),
        ExecutableWitness("db-tests", "python", ("targeted", "full")),
    )
    return ValidationCatalog(
        obligations=(
            ValidationObligation(
                obligation_id="delivery",
                responsibility_paths=("backend/**",),
                responsibility_risk_classes=("backend",),
                required_witness_ids=tuple(item.witness_id for item in witnesses),
                default_depth="targeted",
                full_depth="full",
                omit_allowed=False,
            ),
        ),
        witnesses=witnesses,
        execution_profiles=(
            _profile("container", "docker", container_policy or _policy(max_parallel=1)),
            _profile("python", "linux", python_policy or _policy()),
        ),
    )


def _single_profile_catalog(policy: ShardingPolicy) -> ValidationCatalog:
    return ValidationCatalog(
        obligations=(
            ValidationObligation(
                obligation_id="backend",
                responsibility_paths=("backend/**",),
                responsibility_risk_classes=("backend",),
                required_witness_ids=("python-tests",),
                default_depth="targeted",
                full_depth="full",
                omit_allowed=False,
            ),
        ),
        witnesses=(ExecutableWitness("python-tests", "python", ("targeted", "full")),),
        execution_profiles=(_profile("python", "linux", policy),),
    )


def _manifest(catalog: ValidationCatalog | None = None) -> _TestManifest:
    effective_catalog = catalog or _catalog()
    return build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("db-tests", "api-tests", "container-smoke"),
        tests=(
            ManifestTest("db/b", "db-tests"),
            ManifestTest("api/b", "api-tests"),
            ManifestTest("container/smoke", "container-smoke"),
            ManifestTest("api/a", "api-tests"),
            ManifestTest("db/a", "db-tests"),
        ),
        catalog=effective_catalog,
    )


def _registry(catalog: ValidationCatalog | None = None) -> TargetExecutionRegistry:
    effective_catalog = catalog or _catalog()
    workflow_path = ".github/workflows/ci-coordinator-bootstrap.yml"
    profiles = tuple(
        TargetProfileBinding.from_profile(
            profile,
            workflow_path=workflow_path,
            job_id="selected-" + profile.profile_id.replace(".", "-"),
        )
        for profile in effective_catalog.execution_profiles
    )
    return TargetExecutionRegistry(
        "test-generator",
        "1",
        tuple(
            TargetAdapterFileBinding(path, "0" * 64)
            for path in sorted((workflow_path, *TARGET_CONTROL_FILE_PATHS))
        ),
        (
            TargetWorkflowBinding(
                workflow_path=workflow_path,
                execution_kind="witness-shards",
                execution_jobs=tuple(TargetJobBinding(item.job_id, ("plan",)) for item in profiles),
                plan_request_job_id=PLAN_REQUEST_JOB_ID,
                plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
                plan_job_id="plan",
                fallback_job_id="full-ci",
                gate_job_id="bootstrap-gate",
                gate_signal_name="Dynamic CI Bootstrap",
            ),
        ),
        profiles,
    )


def _snapshot(
    *,
    observed_at: datetime = NOW,
    docker_slots: int = 1,
    linux_slots: int = 2,
) -> RunnerSnapshot:
    return RunnerSnapshot(
        observed_at=observed_at,
        freshness_ttl_seconds=60,
        capacities=(
            RunnerCapacity("docker", docker_slots),
            RunnerCapacity("linux", linux_slots),
        ),
        source="operator",
    )


def _history() -> dict[str, float]:
    return {
        "api/a": 8.0,
        "api/b": 7.0,
        "container/smoke": 4.0,
        "db/a": 6.0,
        "db/b": 5.0,
    }


def test_manifest_resolves_every_witness_through_the_closed_catalog() -> None:
    manifest = _manifest()

    assert [item.profile_id for item in manifest.execution_profiles] == ["container", "python"]
    assert manifest.profile_id_for_test("api/a") == "python"
    assert manifest.profile_id_for_test("container/smoke") == "container"
    assert manifest.manifest_id == manifest.manifest_id


@pytest.mark.parametrize(
    ("selected_witness_ids", "tests", "message"),
    [
        (("missing",), (ManifestTest("api/a", "missing"),), "resolve"),
        (
            ("api-tests", "container-smoke"),
            (ManifestTest("api/a", "api-tests"),),
            "cover every selected witness",
        ),
    ],
)
def test_manifest_rejects_open_or_incomplete_witness_coverage(
    selected_witness_ids: tuple[str, ...],
    tests: tuple[ManifestTest, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_test_manifest(
            verified_plan_id="verified-plan",
            target_registry_hash="a" * 64,
            selected_witness_ids=selected_witness_ids,
            tests=tests,
            catalog=_catalog(),
        )


def test_manifest_projects_complete_inventory_to_selected_witnesses() -> None:
    manifest = build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("api-tests",),
        tests=(
            ManifestTest("api/a", "api-tests"),
            ManifestTest("container/a", "container-smoke"),
            ManifestTest("db/a", "db-tests"),
        ),
        catalog=_catalog(),
    )

    assert manifest.selected_witness_ids == ("api-tests",)
    assert manifest.tests == (ManifestTest("api/a", "api-tests"),)


@pytest.mark.parametrize(
    ("tests", "message"),
    [
        ((ManifestTest("unknown/a", "unknown"),), "resolve"),
        (
            (
                ManifestTest("same", "api-tests"),
                ManifestTest("same", "container-smoke"),
            ),
            "unique",
        ),
    ],
    ids=("unknown-witness", "duplicate-test-id"),
)
def test_manifest_rejects_malformed_complete_inventory(
    tests: tuple[ManifestTest, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_test_manifest(
            verified_plan_id="verified-plan",
            target_registry_hash="a" * 64,
            selected_witness_ids=("api-tests",),
            tests=tests,
            catalog=_catalog(),
        )


def test_optimization_is_profile_homogeneous_and_covers_each_test_once() -> None:
    manifest = _manifest()
    result = optimize_shards(manifest, _snapshot(), _history(), now=NOW)

    assert [item.profile_id for item in result.profile_plans] == ["container", "python"]
    assert all(item.mode == "optimized" for item in result.profile_plans)
    assigned = [
        test_id
        for profile_plan in result.profile_plans
        for shard in profile_plan.shards
        for test_id in shard.test_ids
    ]
    assert sorted(assigned) == [item.test_id for item in manifest.tests]
    assert all(
        manifest.profile_id_for_test(test_id) == profile_plan.profile_id
        for profile_plan in result.profile_plans
        for shard in profile_plan.shards
        for test_id in shard.test_ids
    )
    python = result.profile_plans[1]
    assert python.max_parallel == 2
    assert {shard.test_ids for shard in python.shards} == {
        ("api/a", "db/b"),
        ("api/b", "db/a"),
    }


def test_profiles_share_one_capacity_class_budget_without_double_counting() -> None:
    base = _catalog()
    catalog = ValidationCatalog(
        obligations=base.obligations,
        witnesses=base.witnesses,
        execution_profiles=tuple(
            replace(profile, capacity_class_id="shared") for profile in base.execution_profiles
        ),
    )
    snapshot = RunnerSnapshot(
        NOW,
        60,
        (RunnerCapacity("shared", 2),),
        "operator",
    )

    result = optimize_shards(_manifest(catalog), snapshot, _history(), now=NOW)

    assert [item.max_parallel for item in result.profile_plans] == [1, 1]
    assert sum(item.max_parallel for item in result.profile_plans) == 2


def test_shared_pool_oversubscription_is_explicit_when_profiles_exceed_slots() -> None:
    base = _catalog()
    catalog = ValidationCatalog(
        obligations=base.obligations,
        witnesses=base.witnesses,
        execution_profiles=tuple(
            replace(profile, capacity_class_id="shared") for profile in base.execution_profiles
        ),
    )
    snapshot = RunnerSnapshot(
        NOW,
        60,
        (RunnerCapacity("shared", 1),),
        "operator",
    )

    result = optimize_shards(_manifest(catalog), snapshot, _history(), now=NOW)

    assert [(item.mode, item.reason) for item in result.profile_plans] == [
        ("optimized", None),
        ("conservative", "runner_capacity_shared_pool_exhausted"),
    ]


def test_lpt_optimization_is_deterministic_for_equivalent_history_mappings() -> None:
    manifest = _manifest()
    forward = optimize_shards(manifest, _snapshot(), _history(), now=NOW)
    reverse = optimize_shards(
        manifest,
        _snapshot(),
        dict(reversed(tuple(_history().items()))),
        now=NOW,
    )

    assert forward == reverse


def test_shard_identity_binds_manifest_profile_and_canonical_test_content() -> None:
    base = Shard("manifest-a", "python", ("api/a", "api/b"), 10.0)

    assert base.shard_id.startswith("ci_shard_")
    assert len(base.shard_id) == len("ci_shard_") + 32
    assert base.shard_id == Shard("manifest-a", "python", ("api/a", "api/b"), 999.0).shard_id
    assert base.shard_id != Shard("manifest-b", "python", ("api/a", "api/b"), 10.0).shard_id
    assert base.shard_id != Shard("manifest-a", "container", ("api/a", "api/b"), 10.0).shard_id
    assert base.shard_id != Shard("manifest-a", "python", ("api/a", "db/a"), 10.0).shard_id


def test_shard_identity_changes_when_the_verified_plan_epoch_changes() -> None:
    first_manifest = _manifest()
    second_manifest = replace(first_manifest, verified_plan_id="verified-plan-next")

    first_plan = optimize_shards(first_manifest, None, _history(), now=NOW)
    second_plan = optimize_shards(second_manifest, None, _history(), now=NOW)

    first_ids = {
        shard.shard_id for profile_plan in first_plan.profile_plans for shard in profile_plan.shards
    }
    second_ids = {
        shard.shard_id
        for profile_plan in second_plan.profile_plans
        for shard in profile_plan.shards
    }
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (None, "runner_capacity_unknown"),
        (_snapshot(observed_at=NOW - timedelta(minutes=2)), "runner_capacity_stale"),
        (_snapshot(observed_at=NOW + timedelta(seconds=1)), "runner_capacity_stale"),
    ],
)
def test_unknown_or_stale_capacity_uses_one_serial_shard_per_profile(
    snapshot: RunnerSnapshot | None,
    reason: str,
) -> None:
    result = optimize_shards(_manifest(), snapshot, _history(), now=NOW)

    assert all(len(item.shards) == 1 for item in result.profile_plans)
    assert all(item.max_parallel == 1 for item in result.profile_plans)
    assert all(
        item.mode == "conservative" and item.reason == reason for item in result.profile_plans
    )


def test_missing_capacity_or_history_falls_back_only_for_the_affected_profile() -> None:
    manifest = _manifest()
    without_linux = RunnerSnapshot(
        observed_at=NOW,
        freshness_ttl_seconds=60,
        capacities=(RunnerCapacity("docker", 1),),
        source="operator",
    )
    missing_capacity = optimize_shards(manifest, without_linux, _history(), now=NOW)
    incomplete_history = optimize_shards(
        manifest,
        _snapshot(),
        {"container/smoke": 4.0},
        now=NOW,
    )

    assert [(item.mode, item.reason) for item in missing_capacity.profile_plans] == [
        ("optimized", None),
        ("conservative", "runner_capacity_unknown"),
    ]
    assert [(item.mode, item.reason) for item in incomplete_history.profile_plans] == [
        ("optimized", None),
        ("conservative", "test_duration_history_missing"),
    ]


def test_zero_available_slots_use_profile_local_conservative_execution() -> None:
    result = optimize_shards(
        _manifest(),
        _snapshot(docker_slots=0, linux_slots=0),
        _history(),
        now=NOW,
    )

    assert all(item.mode == "conservative" for item in result.profile_plans)
    assert all(item.reason == "runner_capacity_unavailable" for item in result.profile_plans)
    assert all(item.max_parallel == 1 for item in result.profile_plans)


def test_max_items_per_shard_sets_minimum_cardinality_independent_of_parallelism() -> None:
    policy = _policy(max_shards=3, max_parallel=1, max_items_per_shard=2)
    catalog = _single_profile_catalog(policy)
    tests = tuple(ManifestTest(f"test/{index}", "python-tests") for index in range(5))
    manifest = build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("python-tests",),
        tests=tests,
        catalog=catalog,
    )
    result = optimize_shards(
        manifest,
        RunnerSnapshot(NOW, 60, (RunnerCapacity("linux", 1),), "operator"),
        {item.test_id: float(index + 1) for index, item in enumerate(tests)},
        now=NOW,
    )

    profile_plan = result.profile_plans[0]
    assert len(profile_plan.shards) == 3
    assert profile_plan.max_parallel == 1
    assert all(len(item.test_ids) <= 2 for item in profile_plan.shards)


def test_unknown_capacity_preserves_item_bound_with_minimum_serial_shards() -> None:
    policy = _policy(max_shards=3, max_parallel=3, max_items_per_shard=2)
    catalog = _single_profile_catalog(policy)
    tests = tuple(ManifestTest(f"test/{index}", "python-tests") for index in range(5))
    manifest = build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("python-tests",),
        tests=tests,
        catalog=catalog,
    )

    result = optimize_shards(manifest, None, {}, now=NOW)

    assert len(result.profile_plans[0].shards) == 3
    assert result.profile_plans[0].max_parallel == 1


def test_optimization_fails_closed_when_policy_cannot_represent_exact_coverage() -> None:
    policy = _policy(max_shards=2, max_parallel=2, max_items_per_shard=2)
    catalog = _single_profile_catalog(policy)
    tests = tuple(ManifestTest(f"test/{index}", "python-tests") for index in range(5))
    manifest = build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("python-tests",),
        tests=tests,
        catalog=catalog,
    )

    with pytest.raises(ValueError, match="exceeds the sharding policy bounds"):
        optimize_shards(manifest, None, {}, now=NOW)


def test_profile_plan_rejects_oversized_or_cross_profile_shards() -> None:
    policy = _policy(max_items_per_shard=1)

    with pytest.raises(ValueError, match="max_items_per_shard"):
        ProfileShardPlan(
            profile_id="python",
            sharding_policy=policy,
            shards=(Shard("manifest", "python", ("api/a", "api/b"), 1.0),),
            max_parallel=1,
            mode="conservative",
            reason="test",
        )
    with pytest.raises(ValueError, match="cannot mix"):
        ProfileShardPlan(
            profile_id="python",
            sharding_policy=policy,
            shards=(Shard("manifest", "container", ("api/a",), 1.0),),
            max_parallel=1,
            mode="conservative",
            reason="test",
        )


def test_profile_plan_and_snapshot_reject_unknown_literal_values() -> None:
    policy = _policy()

    with pytest.raises(ValueError, match="mode"):
        ProfileShardPlan(
            profile_id="python",
            sharding_policy=policy,
            shards=(Shard("manifest", "python", ("api/a",), 1.0),),
            max_parallel=1,
            mode="invalid",  # type: ignore[arg-type]
            reason=None,
        )
    with pytest.raises(ValueError, match="source"):
        RunnerSnapshot(
            NOW,
            60,
            (RunnerCapacity("linux", 1),),
            "invalid",  # type: ignore[arg-type]
        )


def test_shard_plan_rejects_cross_profile_assignment_and_dropped_tests() -> None:
    valid = optimize_shards(_manifest(), None, _history(), now=NOW)
    container, python = valid.profile_plans
    wrong_container = replace(
        container,
        shards=(
            Shard(
                valid.manifest_id,
                "container",
                ("api/a", "container/smoke"),
                1.0,
            ),
        ),
    )
    missing_api = replace(
        python,
        shards=(
            Shard(
                valid.manifest_id,
                "python",
                ("api/b", "db/a", "db/b"),
                1.0,
            ),
        ),
    )

    with pytest.raises(ValueError, match="another execution profile"):
        ShardPlan(valid.manifest, (wrong_container, missing_api))
    with pytest.raises(ValueError, match="exactly once"):
        ShardPlan(valid.manifest, (container, missing_api))

    wrong_epoch = replace(
        container,
        shards=tuple(replace(item, manifest_id="other-manifest") for item in container.shards),
    )
    with pytest.raises(ValueError, match="exact test manifest"):
        ShardPlan(valid.manifest, (wrong_epoch, python))


@pytest.mark.parametrize(
    ("test_count", "max_items_per_shard", "expected_shards"),
    [(1, 1, 1), (2, 2, 1), (3, 2, 2), (7, 3, 3), (12, 4, 3)],
)
def test_every_admitted_cardinality_respects_item_and_coverage_bounds(
    test_count: int,
    max_items_per_shard: int,
    expected_shards: int,
) -> None:
    policy = _policy(
        max_shards=expected_shards,
        max_parallel=expected_shards,
        max_items_per_shard=max_items_per_shard,
    )
    catalog = _single_profile_catalog(policy)
    tests = tuple(ManifestTest(f"test/{index:02}", "python-tests") for index in range(test_count))
    manifest = build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("python-tests",),
        tests=tests,
        catalog=catalog,
    )

    result = optimize_shards(manifest, None, {}, now=NOW)
    shards = result.profile_plans[0].shards

    assert len(shards) == expected_shards
    assert all(len(item.test_ids) <= max_items_per_shard for item in shards)
    assert sorted(test_id for item in shards for test_id in item.test_ids) == [
        item.test_id for item in tests
    ]


def test_trusted_inputs_allow_partial_history_but_reject_unowned_entries() -> None:
    tests = (ManifestTest("api/a", "api-tests"),)

    assert TrustedCapacityManifest(tests, ()).history_mapping() == {}
    with pytest.raises(ValueError, match="outside the manifest"):
        TrustedCapacityManifest(tests, (("other", 1.0),))
