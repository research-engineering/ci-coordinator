"""Authorization-first orchestration for effective-governance reads."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation.model import (
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationOutcome,
    GovernanceObservationUnavailable,
    GovernanceState,
)
from ci_coordinator.governance_observation.ports import (
    GovernanceObservationAuthorizer,
    GovernanceStateReader,
)
from ci_coordinator.kernel import Clock


class GovernanceObservationService:
    def __init__(
        self,
        *,
        authorizer: GovernanceObservationAuthorizer,
        reader: GovernanceStateReader,
        clock: Clock,
    ) -> None:
        self._authorizer = authorizer
        self._reader = reader
        self._clock = clock

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceObservationOutcome:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return GovernanceObservationForbidden()
        state = await self._reader.read(scope=scope)
        if isinstance(state, GovernanceObservationUnavailable):
            return state
        if not isinstance(state, GovernanceState):
            raise TypeError("governance reader returned an unsupported outcome")
        return GovernanceObservation.from_state(state, observed_at=self._clock.now())
