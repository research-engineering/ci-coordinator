"""OpenAPI-visible security schemes shared by authenticated HTTP routes."""

from fastapi.security import APIKeyCookie, HTTPBearer

GITHUB_ACTIONS_OIDC_BEARER = HTTPBearer(
    scheme_name="GitHubActionsOIDC",
    bearerFormat="JWT",
    description="GitHub Actions OIDC token bound to the admitted workflow run.",
    auto_error=False,
)

CONTROL_PLANE_BEARER = HTTPBearer(
    scheme_name="ControlPlaneBearer",
    description="Keycloak workload access token or safety-only break-glass credential.",
    auto_error=False,
)

CONTROL_PLANE_SESSION = APIKeyCookie(
    name="__Host-ci_coordinator_session",
    scheme_name="ControlPlaneSession",
    description="Opaque token-free Keycloak browser session.",
    auto_error=False,
)

WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}
WWW_AUTHENTICATE_OPENAPI = {
    "WWW-Authenticate": {
        "description": "Bearer authentication challenge.",
        "schema": {"type": "string"},
    }
}
