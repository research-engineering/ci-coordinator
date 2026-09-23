from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

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
    MAX_CAPACITY_CLASS_SELECTORS,
    CapacityClassSelector,
    ManifestTest,
    ProfileShardPlan,
    RunnerCapacity,
    RunnerSnapshot,
    Shard,
    ShardingPolicy,
    TrustedCapacityManifest,
    TrustedExecutionInputs,
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
    max_shards: int = 2,
    max_parallel: int = 2,
    max_items_per_shard: int = 2,
) -> ShardingPolicy:
    return ShardingPolicy(
        max_shards=max_shards,
        max_parallel=max_parallel,
        max_items_per_shard=max_items_per_shard,
        setup_seconds_per_shard=1.0,
    )


def _manifest() -> _TestManifest:
    policy = _policy()
    catalog = ValidationCatalog(
        obligations=(
            ValidationObligation(
                "backend",
                ("backend/**",),
                ("backend",),
                ("python-tests",),
                "targeted",
                "full",
                False,
            ),
        ),
        witnesses=(ExecutableWitness("python-tests", "python", ("targeted", "full")),),
        execution_profiles=(
            ExecutionProfile(
                "python",
                "linux",
                "read-only",
                "none",
                "none",
                (),
                "standard",
                policy,
            ),
        ),
    )
    return build_test_manifest(
        verified_plan_id="verified-plan",
        target_registry_hash="a" * 64,
        selected_witness_ids=("python-tests",),
        tests=(ManifestTest("test", "python-tests"),),
        catalog=catalog,
    )


def _registry() -> TargetExecutionRegistry:
    workflow_path = ".github/workflows/ci-coordinator-bootstrap.yml"
    profiles = tuple(
        TargetProfileBinding.from_profile(
            profile,
            workflow_path=workflow_path,
            job_id="selected-" + profile.profile_id.replace(".", "-"),
        )
        for profile in _manifest().catalog.execution_profiles
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


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: TrustedCapacityManifest(
                (
                    ManifestTest("b", "witness"),
                    ManifestTest("a", "witness"),
                ),
                (),
            ),
            "canonical",
        ),
        (
            lambda: TrustedCapacityManifest(
                (ManifestTest("a", "witness"),),
                (("a", float("nan")),),
            ),
            "positive finite",
        ),
        (
            lambda: TrustedCapacityManifest(
                (ManifestTest("a", "witness"),),
                (("a", 1.0), ("a", 2.0)),
            ),
            "canonical",
        ),
        (
            lambda: TrustedCapacityManifest(
                (ManifestTest("a", "witness"),),
                (("b", 1.0),),
            ),
            "outside the manifest",
        ),
    ],
)
def test_trusted_capacity_manifest_fails_closed(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_trusted_execution_inputs_admit_absent_capacity_evidence() -> None:
    inputs = TrustedExecutionInputs(_registry(), None)

    assert inputs.capacity_manifest is None


@pytest.mark.parametrize(
    ("selectors", "message"),
    [
        ((CapacityClassSelector("foreign", ("dev",), None),), "sharded target profiles"),
        (
            tuple(
                CapacityClassSelector(f"foreign-{index:02d}", ("dev",), None)
                for index in range(MAX_CAPACITY_CLASS_SELECTORS + 1)
            ),
            "admitted maximum",
        ),
    ],
)
def test_trusted_execution_inputs_reject_unowned_or_unbounded_capacity_selectors(
    selectors: tuple[CapacityClassSelector, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        TrustedExecutionInputs(_registry(), None, selectors)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: RunnerCapacity("linux", -1), "non-negative"),
        (
            lambda: RunnerSnapshot(
                datetime(2026, 7, 17),
                60,
                (RunnerCapacity("linux", 1),),
                "operator",
            ),
            "timezone-aware",
        ),
        (
            lambda: RunnerSnapshot(
                NOW,
                0,
                (RunnerCapacity("linux", 1),),
                "operator",
            ),
            "positive integer",
        ),
        (
            lambda: RunnerSnapshot(
                NOW,
                60,
                (
                    RunnerCapacity("linux", 1),
                    RunnerCapacity("linux", 2),
                ),
                "operator",
            ),
            "canonical",
        ),
    ],
)
def test_runner_snapshot_admission_fails_closed(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: Shard("", "python", ("a",), 1.0), "manifest_id"),
        (lambda: Shard("manifest", "", ("a",), 1.0), "profile_id"),
        (lambda: Shard("manifest", "python", (), 1.0), "test identities"),
        (
            lambda: Shard("manifest", "python", ("a", "a"), 1.0),
            "canonical",
        ),
        (
            lambda: Shard("manifest", "python", ("a",), float("inf")),
            "finite",
        ),
    ],
)
def test_shard_admission_fails_closed(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.parametrize(
    ("max_parallel", "mode", "reason", "message"),
    [
        (0, "conservative", "fallback", "max_parallel"),
        (2, "conservative", "fallback", "max_parallel"),
        (1, "optimized", "unexpected", "no fallback reason"),
        (1, "conservative", None, "require a reason"),
    ],
)
def test_profile_plan_admission_fails_closed(
    max_parallel: int,
    mode: str,
    reason: str | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ProfileShardPlan(
            profile_id="python",
            sharding_policy=_policy(max_parallel=1),
            shards=(Shard("manifest", "python", ("a",), 1.0),),
            max_parallel=max_parallel,
            mode=cast(Any, mode),
            reason=reason,
        )


def test_optimizer_rejects_values_outside_its_nominal_boundary() -> None:
    with pytest.raises(TypeError, match="TestManifest"):
        optimize_shards(cast(Any, object()), None, {}, now=NOW)
    with pytest.raises(TypeError, match="RunnerSnapshot"):
        optimize_shards(
            _manifest(),
            cast(Any, object()),
            {},
            now=NOW,
        )
