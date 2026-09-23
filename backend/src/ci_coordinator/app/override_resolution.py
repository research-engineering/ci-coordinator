"""Planning-facing resolution of durable validation-increasing overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.app.dynamic_plan import DynamicPlanCommand
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import Clock
from ci_coordinator.operator_controls.resolution import (
    ActiveOverrideReader,
    resolve_active_overrides,
)
from ci_coordinator.reconciliation import ReconciliationSubject


@dataclass(frozen=True, slots=True)
class PlanningOverrideDecision:
    force_full_ci: bool
    reason: str | None

    def __post_init__(self) -> None:
        if type(self.force_full_ci) is not bool:
            raise TypeError("override decision must use an exact boolean")
        if self.force_full_ci != (self.reason is not None):
            raise ValueError("forced override decisions require exactly one reason")


class PlanningOverrideResolver(Protocol):
    async def resolve(self, command: DynamicPlanCommand) -> PlanningOverrideDecision: ...


class DurablePlanningOverrideResolver:
    def __init__(self, reader: ActiveOverrideReader, clock: Clock) -> None:
        self._reader = reader
        self._clock = clock

    async def resolve(self, command: DynamicPlanCommand) -> PlanningOverrideDecision:
        request = command.request
        subject = ReconciliationSubject.create(
            installation_id=request.installation_id,
            repository_id=request.repository_id,
            event_name=request.event_name,
            ref=request.ref,
            base_sha=request.base_sha,
            head_sha=request.head_sha,
            workflow_run_id=request.workflow_run_id,
            run_attempt=request.run_attempt,
        )
        resolution = await resolve_active_overrides(
            self._reader,
            scope=RepositoryScope(request.installation_id, request.repository_id),
            subject_id=subject.subject_id,
            now=self._clock.now(),
        )
        if not resolution.lookup_available:
            return PlanningOverrideDecision(True, "override_state_unavailable")
        if resolution.force_full_ci:
            return PlanningOverrideDecision(True, "operator_override")
        return PlanningOverrideDecision(False, None)
