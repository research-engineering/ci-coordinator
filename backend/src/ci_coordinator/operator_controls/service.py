"""Clock-bound application adapter for operator override commands."""

from __future__ import annotations

from ci_coordinator.kernel import Clock
from ci_coordinator.operator_controls.override import OverrideCommand
from ci_coordinator.operator_controls.permissions import OperatorAuthorizer
from ci_coordinator.operator_controls.use_cases import (
    OverrideResult,
    OverrideStore,
    apply_override,
)


class OperatorOverrideService:
    def __init__(
        self,
        *,
        authorizer: OperatorAuthorizer,
        store: OverrideStore,
        clock: Clock,
    ) -> None:
        self._authorizer = authorizer
        self._store = store
        self._clock = clock

    async def __call__(self, command: OverrideCommand) -> OverrideResult:
        return await apply_override(
            command,
            authorizer=self._authorizer,
            store=self._store,
            now=self._clock.now(),
        )
