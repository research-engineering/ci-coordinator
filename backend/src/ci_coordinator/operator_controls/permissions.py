"""Authorization port for governed operator actions."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.override import OverrideKind


class OperatorAuthorizer(Protocol):
    async def allows(self, *, actor: str, action: OverrideKind, scope: RepositoryScope) -> bool: ...
