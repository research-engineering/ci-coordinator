"""Identity admission boundary package."""

from ci_coordinator.identity_admission.actions_oidc import (
    ExpectedActionsOidcClaims,
    TrustedActionsRun,
    admit_verified_actions_oidc_claims,
    is_workflow_path_identity,
    workflow_path_from_ref,
    workflow_path_identity_from_ref,
)
from ci_coordinator.identity_admission.jwks_contracts import (
    JwksFetchResponse,
    JwksProvider,
    JwksProviderConfig,
    JwksTransport,
    JwksTransportFailure,
    JwksUnavailable,
)
from ci_coordinator.identity_admission.jwks_provider import CachingJwksProvider
from ci_coordinator.identity_admission.oidc_header import (
    ActionsOidcHeader,
    RejectedActionsOidcHeader,
    admit_actions_oidc_header,
)
from ci_coordinator.identity_admission.oidc_verifier import ActionsOidcJwkSet, verify_actions_oidc
from ci_coordinator.identity_admission.rejection import RejectedIdentity
from ci_coordinator.identity_admission.webhook_signature import (
    TrustedWebhook,
    verify_webhook,
    verify_webhook_at,
)

__all__ = [
    "ActionsOidcHeader",
    "ActionsOidcJwkSet",
    "CachingJwksProvider",
    "ExpectedActionsOidcClaims",
    "JwksFetchResponse",
    "JwksProvider",
    "JwksProviderConfig",
    "JwksTransport",
    "JwksTransportFailure",
    "JwksUnavailable",
    "RejectedActionsOidcHeader",
    "RejectedIdentity",
    "TrustedActionsRun",
    "TrustedWebhook",
    "admit_actions_oidc_header",
    "admit_verified_actions_oidc_claims",
    "is_workflow_path_identity",
    "verify_actions_oidc",
    "verify_webhook",
    "verify_webhook_at",
    "workflow_path_from_ref",
    "workflow_path_identity_from_ref",
]
