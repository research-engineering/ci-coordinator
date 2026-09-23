"""Effect-free application admission for repository configuration sources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.config_control import (
    PolicyAdmissionResult,
    PolicyDiagnostic,
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
)


class ConfigScopeAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class PolicyAdmissionUnavailable(RuntimeError):
    """The bounded policy-admission worker cannot accept or complete work."""


type PolicyAdmission = Callable[
    [bytes, PolicySourceFormat],
    Awaitable[PolicyAdmissionResult],
]


@dataclass(frozen=True, slots=True)
class ConfigAdmissionAccepted:
    draft: ValidatedEpochDraft

    def __post_init__(self) -> None:
        if type(self.draft) is not ValidatedEpochDraft:
            raise TypeError("config admission requires an exact validated epoch draft")


@dataclass(frozen=True, slots=True)
class ConfigAdmissionInvalid:
    diagnostics: tuple[PolicyDiagnostic, ...]

    def __post_init__(self) -> None:
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not PolicyDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("invalid config admission requires exact policy diagnostics")
        if not self.diagnostics:
            raise ValueError("invalid config admission requires diagnostics")


@dataclass(frozen=True, slots=True)
class ConfigAdmissionForbidden:
    pass


@dataclass(frozen=True, slots=True)
class ConfigAdmissionUnavailable:
    pass


type ConfigAdmissionResult = (
    ConfigAdmissionAccepted
    | ConfigAdmissionInvalid
    | ConfigAdmissionForbidden
    | ConfigAdmissionUnavailable
)


class ConfigAdmissionUseCase(Protocol):
    async def admit(
        self,
        *,
        actor: str,
        source: bytes,
        source_format: PolicySourceFormat,
    ) -> ConfigAdmissionResult: ...


class ConfigAdmissionService:
    """Apply only source and scope admission; this owner has no effect ports."""

    def __init__(
        self,
        *,
        authorizer: ConfigScopeAuthorizer,
        policy_admission: PolicyAdmission,
    ) -> None:
        self._authorizer = authorizer
        self._policy_admission = policy_admission

    async def admit(
        self,
        *,
        actor: str,
        source: bytes,
        source_format: PolicySourceFormat,
    ) -> ConfigAdmissionResult:
        try:
            admitted = await self._policy_admission(source, source_format)
        except PolicyAdmissionUnavailable:
            return ConfigAdmissionUnavailable()
        if isinstance(admitted, tuple):
            return ConfigAdmissionInvalid(admitted)
        if not await self._authorizer.allows_scope(actor=actor, scope=admitted.scope):
            return ConfigAdmissionForbidden()
        return ConfigAdmissionAccepted(admitted)
