"""Ports owned by repository workbench reads."""

from __future__ import annotations

from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workbench_read_models.model import RepositoryDataSnapshot, WorkbenchResult


class WorkbenchAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class WorkbenchRepository(Protocol):
    async def load(self, scope: RepositoryScope, *, limit: int) -> RepositoryDataSnapshot: ...


class WorkbenchUseCase(Protocol):
    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        limit: int,
    ) -> WorkbenchResult: ...


class WorkbenchReadError(RuntimeError):
    pass


class ReplayVerification(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def reason(self) -> str: ...

    @property
    def verified_revision(self) -> int | None: ...


class ReplayVerificationProbe(Protocol):
    async def check(self) -> ReplayVerification: ...
