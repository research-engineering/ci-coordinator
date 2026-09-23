"""Deployment-profile authorization for validation-increasing controls."""

from __future__ import annotations

from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.override import OverrideKind


class ActorAuthority(Protocol):
    def is_administrator(self, actor: object) -> bool: ...

    def is_break_glass(self, actor: object) -> bool: ...


class RepositoryAccessUnavailable(Exception):
    pass


class RepositoryAccessReader(Protocol):
    async def allows_repository(self, scope: RepositoryScope) -> bool: ...


class ControlPlaneScopeAuthorizer:
    def __init__(
        self,
        *,
        actor_authority: ActorAuthority,
        allowed_scopes: frozenset[RepositoryScope],
        scope_mode: Literal["restricted", "app"] = "restricted",
        repository_access: RepositoryAccessReader | None = None,
    ) -> None:
        if type(allowed_scopes) is not frozenset:
            raise ValueError("control-plane authorizer requires frozen repository scopes")
        if scope_mode not in {"restricted", "app"} or (
            (scope_mode == "app") != (repository_access is not None)
        ):
            raise ValueError("App scope admission requires exactly one repository access reader")
        self._actor_authority = actor_authority
        self._allowed_scopes = allowed_scopes
        self._repository_access = repository_access

    async def allows(
        self,
        *,
        actor: str,
        action: OverrideKind,
        scope: RepositoryScope,
    ) -> bool:
        if self._actor_authority.is_break_glass(actor):
            return scope in self._allowed_scopes and action in {"force_full_ci", "disable_omission"}
        return action in {"force_full_ci", "disable_omission", "enable_omission"} and (
            await self.allows_scope(actor=actor, scope=scope)
        )

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        if not self._actor_authority.is_administrator(actor):
            return False
        if self._repository_access is None:
            return scope in self._allowed_scopes
        allowed = await self._repository_access.allows_repository(scope)
        if type(allowed) is not bool:
            raise RepositoryAccessUnavailable()
        return allowed
