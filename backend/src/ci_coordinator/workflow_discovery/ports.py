"""Capabilities required by the workflow-discovery use case."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workflow_discovery.outcomes import (
    SnapshotReadOutcome,
    WorkflowDiscoveryOutcome,
)


class WorkflowDiscoveryUseCase(Protocol):
    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome: ...


class WorkflowDiscoveryAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class WorkflowSnapshotReader(Protocol):
    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> SnapshotReadOutcome: ...
