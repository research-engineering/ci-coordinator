"""Control-plane identity composition without product policy ownership."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.api.http.control_plane_authentication import (
    BreakGlassBearerAuthenticator,
    ControlPlaneRequestAuthenticator,
    ControlPlaneRoleAuthorizer,
)
from ci_coordinator.api.http.control_plane_security import ControlPlaneMutationGuard
from ci_coordinator.control_plane_identity import (
    BrowserIdentityPolicy,
    BrowserIdentityService,
    ControlPlaneIdentityCrypto,
    MachineIdentityPolicy,
    MachineIdentityService,
)
from ci_coordinator.control_plane_identity.activity import IdentityActivityObserver
from ci_coordinator.integrations.keycloak import KeycloakIntegration
from ci_coordinator.kernel import SystemClock, SystemMonotonicClock
from ci_coordinator.persistence import (
    PostgresBackChannelLogoutStore,
    PostgresControlPlaneSessionStore,
)
from ci_coordinator.runtime_settings import (
    ConnectedRuntimeSettings,
    decode_control_plane_session_key,
)


@dataclass(frozen=True, slots=True)
class ControlPlaneRuntimeDependencies:
    authenticator: ControlPlaneRequestAuthenticator
    role_admission: ControlPlaneRoleAuthorizer
    mutation_admission: ControlPlaneMutationGuard
    browser_identity: BrowserIdentityService | None
    identity_crypto: ControlPlaneIdentityCrypto | None
    keycloak: KeycloakIntegration | None


def compose_control_plane_runtime_dependencies(
    *,
    settings: ConnectedRuntimeSettings,
    engine: AsyncEngine,
    clock: SystemClock,
    activity: IdentityActivityObserver | None = None,
) -> ControlPlaneRuntimeDependencies:
    break_glass = BreakGlassBearerAuthenticator(
        actor_id=settings.break_glass_actor_id,
        bearer_token=settings.break_glass_bearer_token.reveal_for_composition(),
    )
    role_admission = ControlPlaneRoleAuthorizer(clock, activity)
    identity = settings.control_plane_identity
    if identity is None:
        return ControlPlaneRuntimeDependencies(
            authenticator=ControlPlaneRequestAuthenticator(
                session_cookie_name=None,
                human=None,
                machine=None,
                break_glass=break_glass,
            ),
            role_admission=role_admission,
            mutation_admission=ControlPlaneMutationGuard(human=None, public_origin=None),
            browser_identity=None,
            identity_crypto=None,
            keycloak=None,
        )

    keycloak = KeycloakIntegration(
        issuer=identity.issuer,
        browser_client_id=identity.browser_client_id,
        browser_client_secret=identity.browser_client_secret.reveal_for_composition(),
        api_client_id=identity.api_client_id,
        redirect_uri=identity.callback_uri,
        post_logout_redirect_uri=identity.post_logout_uri,
        clock=SystemMonotonicClock(),
        outbound_proxy_url=settings.outbound_proxy_url,
    )
    crypto = ControlPlaneIdentityCrypto(decode_control_plane_session_key(identity))
    browser = BrowserIdentityService(
        activity=activity,
        provider=keycloak.browser,
        sessions=PostgresControlPlaneSessionStore(engine),
        back_channel_logouts=PostgresBackChannelLogoutStore(engine),
        crypto=crypto,
        clock=clock,
        policy=BrowserIdentityPolicy(
            issuer=identity.issuer,
            authority_profile_digest=identity.profile_digest,
            session_maximum_seconds=identity.maximum_session_seconds,
        ),
    )
    machine = MachineIdentityService(
        verifier=keycloak.machine,
        clock=clock,
        policy=MachineIdentityPolicy(
            issuer=identity.issuer,
            audience=identity.api_client_id,
            allowed_workload_client_ids=identity.workload_client_ids,
            authority_profile_digest=identity.profile_digest,
        ),
    )
    return ControlPlaneRuntimeDependencies(
        authenticator=ControlPlaneRequestAuthenticator(
            session_cookie_name=identity.session_cookie_name,
            human=browser,
            machine=machine,
            break_glass=break_glass,
        ),
        role_admission=role_admission,
        mutation_admission=ControlPlaneMutationGuard(
            human=browser,
            public_origin=identity.public_origin,
        ),
        browser_identity=browser,
        identity_crypto=crypto,
        keycloak=keycloak,
    )
