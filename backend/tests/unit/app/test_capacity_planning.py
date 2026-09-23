from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from package_b_support import (
    BASE_SHA,
    HEAD_SHA,
    make_catalog,
    make_input,
    make_native_execution_projection,
    make_policy,
    make_target_registry,
)
from package_b_support import assert_deterministic_plan as _candidate

from ci_coordinator.app.capacity_planning import (
    CapacityPlanningService,
    ExecutionPlanningResult,
)
from ci_coordinator.kernel import FixedClock
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.planning_core import plan
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.runner_capacity import (
    CapacityClassSelector,
    ManifestTest,
    RunnerCapacity,
    RunnerSnapshot,
    ShardPlan,
    TrustedCapacityManifest,
    TrustedExecutionInputs,
    TrustedExecutionProjection,
)
from ci_coordinator.validation_contract import ExecutionProfile, ShardingPolicy, ValidationCatalog
from ci_coordinator.verification_core import VerifiedPlan, verify

NOW = datetime(2026, 7, 15, tzinfo=UTC)
WORKFLOW_SHA = "e" * 40


class StaticInputs:
    def __init__(self, value: TrustedExecutionInputs | None) -> None:
        self._value = value
        self.calls = 0

    async def load(
        self,
        _request: PlanRequest,
        _execution_authority_sha: str,
        /,
    ) -> TrustedExecutionInputs | None:
        self.calls += 1
        return self._value


class FailingInputs:
    async def load(
        self,
        _request: PlanRequest,
        _execution_authority_sha: str,
        /,
    ) -> TrustedExecutionInputs | None:
        raise RuntimeError("trusted input store unavailable")


class CancelledInputs:
    async def load(
        self,
        _request: PlanRequest,
        _execution_authority_sha: str,
        /,
    ) -> TrustedExecutionInputs | None:
        raise asyncio.CancelledError


class StaticSnapshots:
    def __init__(self, value: RunnerSnapshot | None) -> None:
        self._value = value

    async def capture(
        self,
        _request: PlanRequest,
        _selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> RunnerSnapshot | None:
        return self._value


class FailingSnapshots:
    async def capture(
        self,
        _request: PlanRequest,
        _selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> RunnerSnapshot | None:
        raise AssertionError("native job selection must not inspect runner capacity")


class ExceptionalSnapshots:
    async def capture(
        self,
        _request: PlanRequest,
        _selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> RunnerSnapshot | None:
        raise RuntimeError("snapshot provider unavailable")


def _snapshot(
    observed_at: datetime,
    *,
    free_slots: int,
    source: Literal["github-self-hosted", "operator"] = "github-self-hosted",
) -> RunnerSnapshot:
    return RunnerSnapshot(
        observed_at=observed_at,
        freshness_ttl_seconds=60,
        capacities=(
            RunnerCapacity("docs-self-hosted", 1),
            RunnerCapacity("self-hosted-default", free_slots),
        ),
        source=source,
    )


def test_fresh_capacity_can_increase_parallelism_without_changing_coverage() -> None:
    verified = _verified_plan()
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(_snapshot(NOW, free_slots=2)),
        inputs=StaticInputs(_trusted_inputs(verified)),
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))

    execution = _require_execution(result)
    shard_plan = execution.shard_plan
    assert isinstance(shard_plan, ShardPlan)
    assert shard_plan.verified_plan_id == verified.execution_plan_id
    assert shard_plan.manifest.selected_witness_ids == tuple(
        witness.witness_id for witness in verified.selected_witnesses
    )
    assert {plan.profile_id: plan.max_parallel for plan in shard_plan.profile_plans} == {
        "docs-default": 1,
        "python-default": 2,
    }
    _assert_exact_profile_homogeneous_coverage(shard_plan)


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        _snapshot(NOW - timedelta(minutes=2), free_slots=8, source="operator"),
    ],
    ids=["unknown", "stale"],
)
def test_unknown_or_stale_capacity_is_conservative(
    snapshot: RunnerSnapshot | None,
) -> None:
    verified = _verified_plan()
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(snapshot),
        inputs=StaticInputs(_trusted_inputs(verified)),
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))

    execution = _require_execution(result)
    shard_plan = execution.shard_plan
    assert isinstance(shard_plan, ShardPlan)
    expected_reason = "runner_capacity_unknown" if snapshot is None else "runner_capacity_stale"
    assert all(plan.max_parallel == 1 for plan in shard_plan.profile_plans)
    assert all(plan.mode == "conservative" for plan in shard_plan.profile_plans)
    assert all(plan.reason == expected_reason for plan in shard_plan.profile_plans)
    _assert_exact_profile_homogeneous_coverage(shard_plan)


def test_native_job_selection_does_not_fabricate_shards_or_read_capacity() -> None:
    verified = _verified_plan()
    native = make_native_execution_projection(verified)
    inputs = replace(
        _trusted_inputs(verified),
        target_registry=native.target_registry,
        capacity_manifest=None,
        capacity_selectors=(),
    )
    service = CapacityPlanningService(
        snapshots=FailingSnapshots(),
        inputs=StaticInputs(inputs),
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))

    execution = _require_execution(result)
    assert execution.execution_kind == "native-job-set"
    assert execution.workflow_path == ".github/workflows/full-check.yml"
    assert execution.shard_plan is None


def test_dependency_failure_withholds_unverified_shard_projection() -> None:
    verified = _verified_plan()
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(_snapshot(NOW, free_slots=8, source="operator")),
        inputs=FailingInputs(),
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))

    assert result is None


def test_snapshot_exception_preserves_target_but_withholds_selected_execution() -> None:
    verified = _verified_plan()
    inputs = _trusted_inputs(verified)
    service = CapacityPlanningService(
        snapshots=ExceptionalSnapshots(),
        inputs=StaticInputs(inputs),
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))

    assert isinstance(result, ExecutionPlanningResult)
    assert result.execution is None
    assert result.target.provider_signal == inputs.target_registry.workflows[0].provider_signal


def test_manifest_without_every_selected_witness_is_rejected_conservatively() -> None:
    verified = _verified_plan()
    inputs = _trusted_inputs(verified)
    assert inputs.capacity_manifest is not None
    missing_witness_id = verified.selected_witnesses[-1].witness_id
    retained_tests = tuple(
        item for item in inputs.capacity_manifest.tests if item.witness_id != missing_witness_id
    )
    retained_test_ids = {item.test_id for item in retained_tests}
    incomplete = replace(
        inputs,
        capacity_manifest=TrustedCapacityManifest(
            tests=retained_tests,
            duration_history=tuple(
                item
                for item in inputs.capacity_manifest.duration_history
                if item[0] in retained_test_ids
            ),
        ),
    )
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(_snapshot(NOW, free_slots=8)),
        inputs=StaticInputs(incomplete),
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))
    assert isinstance(result, ExecutionPlanningResult)
    assert result.execution is None
    assert result.target.provider_signal == inputs.target_registry.workflows[0].provider_signal


def test_target_registry_must_bind_every_selected_execution_profile() -> None:
    verified = _verified_plan()
    inputs = _trusted_inputs(verified)
    mismatched = replace(
        inputs,
        target_registry=make_target_registry(make_catalog()),
        capacity_selectors=(),
    )
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(_snapshot(NOW, free_slots=8)),
        inputs=StaticInputs(mismatched),
        clock=FixedClock(NOW),
    )

    assert asyncio.run(service.project(verified, _request(), WORKFLOW_SHA)) is None


@pytest.mark.parametrize(
    "execution_authority_sha",
    [None, "", "A" * 40, "a" * 39, "a" * 65],
    ids=["absent", "empty", "uppercase", "short", "long"],
)
def test_capacity_planning_requires_a_canonical_caller_workflow_revision(
    execution_authority_sha: str | None,
) -> None:
    verified = _verified_plan()
    inputs = StaticInputs(_trusted_inputs(verified))
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(_snapshot(NOW, free_slots=8)),
        inputs=inputs,
        clock=FixedClock(NOW),
    )

    assert asyncio.run(service.project(verified, _request(), execution_authority_sha)) is None
    assert inputs.calls == 0


def test_cancellation_propagates() -> None:
    verified = _verified_plan()
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(None),
        inputs=CancelledInputs(),
        clock=FixedClock(NOW),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))


def test_local_manifest_failure_is_not_misclassified_as_input_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = _verified_plan()

    def fail_manifest(**_kwargs: object) -> None:
        raise AssertionError("local manifest defect")

    monkeypatch.setattr(
        "ci_coordinator.app.capacity_planning.build_test_manifest",
        fail_manifest,
    )
    service = CapacityPlanningService(
        snapshots=StaticSnapshots(None),
        inputs=StaticInputs(_trusted_inputs(verified)),
        clock=FixedClock(NOW),
    )

    with pytest.raises(AssertionError, match="local manifest defect"):
        asyncio.run(service.project(verified, _request(), WORKFLOW_SHA))


def _trusted_inputs(verified: VerifiedPlan) -> TrustedExecutionInputs:
    manifest_tests = tuple(
        ManifestTest(f"{witness.witness_id}/test", witness.witness_id)
        for witness in verified.catalog.witnesses
    )
    return TrustedExecutionInputs(
        target_registry=make_target_registry(verified.catalog),
        capacity_manifest=TrustedCapacityManifest(
            tests=manifest_tests,
            duration_history=tuple((item.test_id, 10.0) for item in manifest_tests),
        ),
        capacity_selectors=(
            CapacityClassSelector("docs-self-hosted", ("docs",), None),
            CapacityClassSelector("self-hosted-default", ("python",), None),
        ),
    )


def _require_execution(
    value: ExecutionPlanningResult | None,
) -> TrustedExecutionProjection:
    assert isinstance(value, ExecutionPlanningResult)
    assert isinstance(value.execution, TrustedExecutionProjection)
    return value.execution


def _verified_plan() -> VerifiedPlan:
    planning_input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy(catalog=_two_profile_catalog())
    return verify(planning_input, policy, _candidate(plan(planning_input, policy)))


def _two_profile_catalog() -> ValidationCatalog:
    catalog = make_catalog()
    policy = ShardingPolicy(
        max_shards=8,
        max_parallel=8,
        max_items_per_shard=10_000,
        setup_seconds_per_shard=0.0,
        cpu_weight=0.0,
        wall_weight=1.0,
        operator_weight=0.0,
    )
    python_profile = replace(catalog.execution_profiles[0], sharding_policy=policy)
    docs_profile = ExecutionProfile(
        profile_id="docs-default",
        runner_profile_id="ubuntu-24.04",
        permission_profile_id="contents-read",
        credential_profile_id="none",
        fixture_profile_id="unit",
        service_profile_ids=(),
        capacity_class_id="docs-self-hosted",
        sharding_policy=policy,
    )
    witnesses = tuple(
        replace(witness, execution_profile_id="docs-default")
        if witness.witness_id == "docs-witness"
        else witness
        for witness in catalog.witnesses
    )
    return ValidationCatalog(
        obligations=catalog.obligations,
        witnesses=witnesses,
        execution_profiles=(docs_profile, python_profile),
    )


def _assert_exact_profile_homogeneous_coverage(shard_plan: ShardPlan) -> None:
    witness_profile = {
        witness.witness_id: witness.execution_profile_id
        for witness in shard_plan.manifest.catalog.witnesses
    }
    test_witness = {test.test_id: test.witness_id for test in shard_plan.manifest.tests}
    assigned_test_ids: list[str] = []
    for profile_plan in shard_plan.profile_plans:
        for shard in profile_plan.shards:
            assigned_test_ids.extend(shard.test_ids)
            assert shard.profile_id == profile_plan.profile_id
            assert {witness_profile[test_witness[test_id]] for test_id in shard.test_ids} == {
                profile_plan.profile_id
            }
    assert sorted(assigned_test_ids) == sorted(test_witness)


def _request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        42,
        execution_sha=HEAD_SHA,
    )
