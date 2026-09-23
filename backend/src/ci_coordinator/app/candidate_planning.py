"""Deterministic candidate planning from one active configuration epoch."""

from __future__ import annotations

import asyncio
from typing import Protocol

from ci_coordinator.app.dynamic_plan import DynamicPlanCommand
from ci_coordinator.config_control.planning_projection import (
    DynamicCiPlanningProjection,
    project_dynamic_ci_planning,
)
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot
from ci_coordinator.observability import RuntimeDiagnosticObserver, RuntimeMetrics
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.planning_core import (
    PlanningPolicy,
    PlanningRejected,
    full_ci_fallback_plan,
    plan,
)
from ci_coordinator.repo_context import PlanningInput, PolicySnapshot
from ci_coordinator.verification_core import VerifiedPlan, verify


class LoadedRepositoryContext(Protocol):
    @property
    def planning_input(self) -> PlanningInput: ...


class RepositoryContextProvider(Protocol):
    async def load(
        self,
        request: PlanRequest,
        projection: DynamicCiPlanningProjection | PolicySnapshot,
    ) -> LoadedRepositoryContext: ...


class DeterministicCandidatePlanner:
    """Build a verified candidate or withhold it so issuance falls back to Full CI."""

    def __init__(
        self,
        contexts: RepositoryContextProvider,
        runtime_metrics: RuntimeMetrics | None = None,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> None:
        self._contexts = contexts
        self._runtime_metrics = runtime_metrics
        self._diagnostics = diagnostics

    async def build_candidate(
        self,
        command: DynamicPlanCommand,
        active_epoch: ActiveConfigEpochSnapshot,
    ) -> VerifiedPlan | None:
        projection = project_dynamic_ci_planning(active_epoch.draft)
        if projection is None:
            return None
        policy = PlanningPolicy.from_projection(projection)
        try:
            context = await self._contexts.load(command.request, projection)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._runtime_metrics is not None:
                self._runtime_metrics.planning_unavailable("candidate", "candidate_exception")
            if self._diagnostics is not None:
                self._diagnostics.unexpected_failure("candidate_context", error)
            return None
        deterministic = plan(context.planning_input, policy)
        if isinstance(deterministic, PlanningRejected):
            deterministic = full_ci_fallback_plan(
                context.planning_input,
                policy,
                "planner_rejected:" + ";".join(deterministic.reasons),
            )
        verified = verify(context.planning_input, policy, deterministic)
        if verified.fallback.triggered and self._runtime_metrics is not None:
            self._runtime_metrics.verifier_rejection(verified.fallback.reason)
        return verified
