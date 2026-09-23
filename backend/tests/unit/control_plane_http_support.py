from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request

from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    InvalidCredential,
)
from ci_coordinator.control_plane_identity import (
    CONTROL_PLANE_ROLES,
    ControlPlanePrincipal,
    ControlPlaneRole,
    DisplayMetadata,
    KeycloakHumanPrincipal,
    RoleAdmission,
    admit_control_plane_roles,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def human_principal(
    *,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
) -> KeycloakHumanPrincipal:
    return KeycloakHumanPrincipal(
        issuer="https://auth.example/realms/coordinator",
        subject="test-operator",
        keycloak_session_id="session-1",
        roles=roles,
        session_handle="s" * 43,
        expires_at=NOW + timedelta(hours=1),
        authority_profile_digest="a" * 64,
        display=DisplayMetadata("operator", "Test Operator"),
    )


ACTOR = human_principal().actor_id


@dataclass(frozen=True, slots=True)
class StaticControlPlaneAuthenticator:
    result: ControlPlanePrincipal | InvalidCredential | AuthenticationDependencyUnavailable

    async def authenticate(
        self,
        request: Request,
    ) -> ControlPlanePrincipal | InvalidCredential | AuthenticationDependencyUnavailable:
        del request
        return self.result


@dataclass(frozen=True, slots=True)
class StaticRoleAdmission:
    def admit(
        self,
        principal: ControlPlanePrincipal,
        required_roles: frozenset[ControlPlaneRole],
    ) -> RoleAdmission:
        return admit_control_plane_roles(principal, required_roles, at=NOW)


@dataclass(frozen=True, slots=True)
class StaticMutationAdmission:
    admitted: bool = True

    def admits(self, request: Request, principal: ControlPlanePrincipal) -> bool:
        del request, principal
        return self.admitted
