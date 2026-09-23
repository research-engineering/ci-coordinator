from __future__ import annotations

from scripts.control_plane_profile_values import require_exact_json_value
from scripts.proofkit_common import as_object

_EXPECTED_KEYCLOAK: dict[str, object] = {
    "issuer": {
        "configurationOwner": "deployment",
        "environmentVariable": "CI_COORDINATOR_KEYCLOAK_ISSUER",
        "format": "canonical-https-url",
        "forbiddenComponents": ["fragment", "query", "userinfo", "whitespace"],
        "maximumBytes": 512,
        "matching": "exact",
    },
    "minimumServerVersion": "26.7.3",
    "browserClientId": "ci-coordinator-admin-ui",
    "apiClientId": "ci-coordinator-admin-api",
    "roleClaimPath": "resource_access.ci-coordinator-admin-api.roles",
    "browser": {
        "grant": "authorization_code",
        "pkceMethod": "S256",
        "clientType": "confidential",
        "implicitFlow": False,
        "directAccessGrant": False,
        "retainAccessToken": False,
        "retainRefreshToken": False,
        "sessionMaximumSeconds": 900,
        "transactionMaximumSeconds": 300,
        "sessionHandleEntropyBytes": 32,
        "csrfEntropyBytes": 32,
        "clientAuthentication": {
            "credentialKind": "keycloak-browser-client-secret",
            "method": "client_secret_basic",
            "custodyOwner": "deployment",
            "rotation": "drain-auth-ingress-regenerate-deploy-all-prove-restore",
            "partiallyRotatedReplicaSet": "rejected",
        },
        "cookie": {
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
            "path": "/",
        },
    },
    "backChannelLogout": {
        "expirationAfterIssuedAtMaximumSeconds": 120,
        "maximumTokenAgeSeconds": 120,
        "rejectNonce": True,
        "replayRetentionAfterIssuedAtSeconds": 180,
        "requireExpiration": True,
        "requiredEvent": "http://schemas.openid.net/event/backchannel-logout",
        "requireSidOrSub": True,
    },
    "machine": {
        "grant": "client_credentials",
        "credentialKind": "keycloak-workload-client-credential",
        "oneClientPerWorkload": True,
        "allowedWorkloadClientIds": [],
        "preferredClientAuthentication": ["private_key_jwt", "tls_client_auth"],
        "weakerDeploymentProfile": "client_secret_basic",
    },
    "tokenAdmission": {
        "allowedSigningAlgorithms": ["RS256"],
        "clockSkewSeconds": 60,
        "discoveryCacheMaximumSeconds": 300,
        "maximumTokenBytes": 16384,
        "discoveryMaximumBytes": 262144,
        "jwksCacheMaximumSeconds": 300,
        "jwksMaximumBytes": 1048576,
        "jwksMaximumKeys": 64,
        "machineTokenLifetimeMaximumSeconds": 300,
        "requestTimeoutSeconds": 10,
        "unknownKidRefreshes": 1,
    },
}

_EXPECTED_ATTRIBUTION: dict[str, object] = {
    "humanNamespace": "keycloak-human:v1",
    "machineNamespace": "keycloak-workload:v1",
    "reviewerNamespace": "github-reviewer:v1",
    "breakGlassNamespace": "break-glass:v1",
    "displayMetadataIsAuthority": False,
    "providerEffectsRetainInitiatingActor": True,
}


def validate_identity_profile(keycloak_value: object, attribution_value: object) -> None:
    keycloak = as_object(keycloak_value, "Keycloak profile")
    require_exact_json_value(keycloak, _EXPECTED_KEYCLOAK, "Keycloak profile")

    attribution = as_object(attribution_value, "actor attribution")
    require_exact_json_value(attribution, _EXPECTED_ATTRIBUTION, "actor attribution")
