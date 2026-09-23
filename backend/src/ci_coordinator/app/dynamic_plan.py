"""Application command boundary for signed dynamic-plan issuance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.plan_issuance import IssuanceResult, PlanRequest


@dataclass(frozen=True, slots=True)
class DynamicPlanCommand:
    request: PlanRequest
    identity: TrustedActionsRun

    def __post_init__(self) -> None:
        if type(self.request) is not PlanRequest:
            raise TypeError("dynamic plan command requires a parsed plan request")
        if type(self.identity) is not TrustedActionsRun:
            raise TypeError("dynamic plan command requires an authenticated Actions run")


class DynamicPlanUseCase(Protocol):
    async def request_dynamic_plan(self, command: DynamicPlanCommand) -> IssuanceResult: ...
