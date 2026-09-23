from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol

from ci_coordinator.control_plane_identity.model import (
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
)

type SessionEndReason = Literal["logout", "expired", "revoked", "replaced"]
type SecurityAction = Literal[
    "login", "logout", "expired", "revoked", "replaced", "role_denied", "export"
]
type LoginDiagnostic = Literal["login_rejected", "login_unavailable"]
type ActivityPrincipal = KeycloakHumanPrincipal | KeycloakWorkloadPrincipal


class ActivityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActivityForbidden:
    pass


class IdentityActivityObserver(Protocol):
    async def login_diagnostic(self, action: LoginDiagnostic) -> None: ...

    async def principal_diagnostic(
        self, principal: ActivityPrincipal, action: Literal["role_denied", "export"]
    ) -> None: ...


async def observe_login(observer: IdentityActivityObserver | None, action: LoginDiagnostic) -> None:
    if observer is None:
        return
    try:
        async with asyncio.timeout(0.5):
            await observer.login_diagnostic(action)
    except asyncio.CancelledError:
        raise
    except Exception:
        return


async def observe_principal(
    observer: IdentityActivityObserver | None,
    principal: ActivityPrincipal,
    action: Literal["role_denied", "export"],
) -> None:
    if observer is None:
        return
    try:
        async with asyncio.timeout(0.5):
            await observer.principal_diagnostic(principal, action)
    except asyncio.CancelledError:
        raise
    except Exception:
        return
