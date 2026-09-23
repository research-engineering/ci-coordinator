"""Durable capabilities required by governance-baseline orchestration."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline.acceptance import PreparedGovernanceBaseline
from ci_coordinator.governance_baseline.model import (
    GovernanceBaselineCommand,
    GovernanceBaselineRecord,
    GovernanceBaselineResolution,
    GovernanceBaselineWriteResult,
)


class GovernanceBaselineStoreUnavailable(RuntimeError):
    """The configured durable baseline store cannot complete the operation."""


class GovernanceBaselineStore(Protocol):
    async def resolve_operation(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineResolution: ...

    async def load_active(self, scope: RepositoryScope) -> GovernanceBaselineRecord | None: ...

    async def accept(
        self,
        prepared: PreparedGovernanceBaseline,
    ) -> GovernanceBaselineWriteResult: ...
