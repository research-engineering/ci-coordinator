"""Capabilities required by read-only governance observation."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation.model import (
    GovernanceObservationOutcome,
    GovernanceReadResult,
)


class GovernanceObservationUseCase(Protocol):
    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceObservationOutcome: ...


class GovernanceObservationAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class GovernanceStateReader(Protocol):
    async def read(self, *, scope: RepositoryScope) -> GovernanceReadResult: ...
