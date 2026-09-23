"""Browser-only admission before personalized workbench HTML delivery."""

from fastapi import Request
from fastapi.responses import Response

from ci_coordinator.api.http.control_plane_authentication import (
    ControlPlaneRoleAuthorizer,
    cookie_values,
)
from ci_coordinator.api.http.dependencies import ControlPlaneIdentityRouteDependencies
from ci_coordinator.api.http.routers.control_plane_identity import KEYCLOAK_LOGIN_START_PATH
from ci_coordinator.control_plane_identity import (
    IdentityRejected,
    IdentityUnavailable,
    KeycloakHumanPrincipal,
    RoleAdmissionGranted,
)


async def admit_operator_page(
    request: Request,
    dependencies: ControlPlaneIdentityRouteDependencies,
) -> Response | None:
    sessions = cookie_values(request, dependencies.session_cookie_name)
    if request.headers.getlist("authorization") or len(sessions) > 1:
        return Response(status_code=403)
    if not sessions:
        return _login_redirect()
    principal = await dependencies.identity.authenticate(sessions[0])
    if isinstance(principal, IdentityUnavailable):
        return Response(status_code=503)
    if isinstance(principal, IdentityRejected):
        if principal.code == "overloaded":
            return Response(status_code=503)
        if principal.code in {"unauthenticated", "authority_profile_changed"}:
            return _login_redirect()
        raise RuntimeError("unsupported browser authentication rejection")
    if not isinstance(principal, KeycloakHumanPrincipal):
        raise RuntimeError("unsupported browser authentication outcome")
    admission = dependencies.role_admission.admit(principal, frozenset({"read"}))
    if isinstance(admission, RoleAdmissionGranted):
        return None
    if isinstance(admission, IdentityRejected):
        if admission.code == "unauthenticated":
            return _login_redirect()
        if admission.code == "forbidden":
            if isinstance(dependencies.role_admission, ControlPlaneRoleAuthorizer):
                await dependencies.role_admission.record_denial(principal)
            return Response(status_code=403)
    raise RuntimeError("unsupported workbench role-admission outcome")


def _login_redirect() -> Response:
    return Response(status_code=307, headers={"Location": KEYCLOAK_LOGIN_START_PATH})
