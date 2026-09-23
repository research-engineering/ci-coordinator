"""Coverage-preserving execution parallelism projection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ci_coordinator.execution_orchestration import TrustedExecutionTarget
from ci_coordinator.kernel import Clock, utf16_sort_key
from ci_coordinator.observability import RuntimeDiagnosticObserver, RuntimeMetrics
from ci_coordinator.plan_issuance.model import PlanRequest
from ci_coordinator.runner_capacity import (
    CapacityClassSelector,
    RunnerSnapshot,
    TrustedExecutionInputs,
    TrustedExecutionProjection,
    build_test_manifest,
    optimize_shards,
)
from ci_coordinator.verification_core import VerifiedPlan


class TrustedExecutionInputsProvider(Protocol):
    async def load(
        self,
        request: PlanRequest,
        execution_authority_sha: str,
        /,
    ) -> TrustedExecutionInputs | None: ...


class RequestRunnerSnapshotProvider(Protocol):
    async def capture(
        self,
        request: PlanRequest,
        selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> RunnerSnapshot | None: ...


class UnprovenRunnerSnapshotProvider:
    """Withhold capacity until workload-to-runner eligibility has an exact owner."""

    async def capture(
        self,
        request: PlanRequest,
        selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> None:
        if (
            type(request) is not PlanRequest
            or type(selectors) is not tuple
            or any(type(selector) is not CapacityClassSelector for selector in selectors)
        ):
            raise TypeError("runner snapshot requires an exact request and selector tuple")
        return None


@dataclass(frozen=True, slots=True)
class ExecutionPlanningResult:
    target: TrustedExecutionTarget
    execution: TrustedExecutionProjection | None

    def __post_init__(self) -> None:
        if type(self.target) is not TrustedExecutionTarget:
            raise TypeError("execution planning requires an exact target")
        if self.execution is not None:
            if type(self.execution) is not TrustedExecutionProjection:
                raise TypeError("execution planning projection must be exact")
            if self.execution.target != self.target:
                raise ValueError("execution planning target and projection do not match")


class CapacityPlanningService:
    """Project trusted capacity data without changing verified check coverage."""

    def __init__(
        self,
        *,
        snapshots: RequestRunnerSnapshotProvider,
        inputs: TrustedExecutionInputsProvider,
        clock: Clock,
        runtime_metrics: RuntimeMetrics | None = None,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._inputs = inputs
        self._clock = clock
        self._runtime_metrics = runtime_metrics
        self._diagnostics = diagnostics

    async def project(
        self,
        verified_plan: VerifiedPlan,
        request: PlanRequest,
        execution_authority_sha: str | None,
    ) -> ExecutionPlanningResult | None:
        if type(verified_plan) is not VerifiedPlan or type(request) is not PlanRequest:
            raise TypeError("capacity planning requires exact verified plan and request values")
        if (
            type(execution_authority_sha) is not str
            or not 40 <= len(execution_authority_sha) <= 64
            or any(character not in "0123456789abcdef" for character in execution_authority_sha)
        ):
            self._observe_unavailable("execution_authority_revision_unavailable")
            return None
        try:
            inputs = await self._inputs.load(request, execution_authority_sha)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_unavailable("capacity_inputs_exception")
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("capacity_inputs", error)
            return None
        if type(inputs) is not TrustedExecutionInputs:
            self._observe_unavailable("execution_inputs_unavailable")
            return None
        if not inputs.target_registry.admits(verified_plan.catalog):
            self._observe_unavailable("target_execution_registry_mismatch")
            return None
        selected_profile_ids = _selected_profile_ids(verified_plan)
        bindings = {
            item.profile_id: item
            for item in inputs.target_registry.profiles
            if item.profile_id in selected_profile_ids
        }
        workflow_paths = {item.workflow_path for item in bindings.values()}
        if len(bindings) != len(selected_profile_ids) or len(workflow_paths) != 1:
            self._observe_unavailable("target_execution_workflow_mismatch")
            return None
        workflow_path = workflow_paths.pop()
        workflow = inputs.target_registry.workflow(workflow_path)
        if workflow is None:
            self._observe_unavailable("target_execution_workflow_mismatch")
            return None
        try:
            target = TrustedExecutionTarget(
                verified_plan_id=verified_plan.execution_plan_id,
                workflow_path=workflow_path,
                selected_profile_ids=selected_profile_ids,
                target_registry=inputs.target_registry,
            )
        except ValueError:
            self._observe_unavailable("required_target_execution_unselected")
            return None
        if workflow.execution_kind == "native-job-set":
            execution = TrustedExecutionProjection(
                target=target,
                shard_plan=None,
            )
            return ExecutionPlanningResult(
                target=target,
                execution=execution,
            )
        capacity_manifest = inputs.capacity_manifest
        if capacity_manifest is None:
            self._observe_unavailable("capacity_manifest_unavailable")
            return ExecutionPlanningResult(target=target, execution=None)
        try:
            manifest = build_test_manifest(
                verified_plan_id=verified_plan.execution_plan_id,
                target_registry_hash=inputs.target_registry.registry_hash,
                selected_witness_ids=(item.witness_id for item in verified_plan.selected_witnesses),
                tests=capacity_manifest.tests,
                catalog=verified_plan.catalog,
            )
        except ValueError:
            self._observe_unavailable("capacity_manifest_invalid")
            return ExecutionPlanningResult(target=target, execution=None)
        try:
            selected_capacity_classes = {binding.capacity_class_id for binding in bindings.values()}
            selectors = tuple(
                selector
                for selector in inputs.capacity_selectors
                if selector.capacity_class_id in selected_capacity_classes
            )
            snapshot = await self._snapshots.capture(request, selectors)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._observe_unavailable("runner_snapshot_exception")
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("runner_snapshot", error)
            return ExecutionPlanningResult(target=target, execution=None)
        now = self._clock.now()
        self._observe_snapshot(snapshot, now=now)
        shard_plan = optimize_shards(
            manifest,
            snapshot,
            capacity_manifest.history_mapping(),
            now=now,
        )
        if self._runtime_metrics is not None:
            for profile_plan in shard_plan.profile_plans:
                self._runtime_metrics.capacity_decision(
                    profile_plan.mode,
                    profile_plan.reason,
                )
        execution = TrustedExecutionProjection(
            target=target,
            shard_plan=shard_plan,
        )
        return ExecutionPlanningResult(
            target=target,
            execution=execution,
        )

    def _observe_unavailable(self, reason: str) -> None:
        if self._runtime_metrics is not None:
            self._runtime_metrics.capacity_decision("conservative", reason)
        return None

    def _observe_snapshot(self, snapshot: RunnerSnapshot | None, *, now: datetime) -> None:
        if self._runtime_metrics is None:
            return
        self._runtime_metrics.runner_snapshot(_snapshot_state(snapshot, now))


def _snapshot_state(snapshot: RunnerSnapshot | None, now: datetime) -> str:
    if snapshot is None:
        return "missing"
    return "fresh" if snapshot.is_fresh(now) else "stale"


def _selected_profile_ids(verified_plan: VerifiedPlan) -> tuple[str, ...]:
    profile_by_witness = {
        item.witness_id: item.execution_profile_id for item in verified_plan.catalog.witnesses
    }
    return tuple(
        sorted(
            {profile_by_witness[item.witness_id] for item in verified_plan.selected_witnesses},
            key=utf16_sort_key,
        )
    )
