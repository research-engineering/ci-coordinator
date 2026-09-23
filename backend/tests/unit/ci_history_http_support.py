from unittest.mock import AsyncMock

from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    AuthenticationDependencyUnavailable,
    CiHistoryRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.control_plane_identity import CONTROL_PLANE_ROLES, ControlPlaneRole


def history_app(
    use_case: AsyncMock,
    *,
    authority: str = "valid",
    integrity: bool = True,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
) -> FastAPI:
    return create_app(
        HttpRouteDependencies(
            ci_history=CiHistoryRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(
                    InvalidCredential()
                    if authority == "invalid"
                    else AuthenticationDependencyUnavailable()
                    if authority == "unavailable"
                    else human_principal(roles=roles)
                ),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(integrity),
                use_case=use_case,
            )
        ),
        include_operator_ui=False,
        request_timeout_seconds=30,
    )
