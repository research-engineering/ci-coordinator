from __future__ import annotations

from collections.abc import Mapping

from scripts.control_plane_profile_values import require_exact_json_value
from scripts.proofkit_common import as_object

_EXPECTED_GITHUB_APP: dict[str, object] = {
    "ownerOrganization": {
        "configurationOwner": "deployment",
        "requiredFields": ["id", "login"],
    },
    "appAuthentication": {
        "installationTokenCredentialKind": "github-installation-token",
        "jwtCredentialKind": "github-app-jwt",
        "privateKeyCredentialKind": "github-app-private-key",
        "privateKeyCustodyOwner": "deployment",
        "privateKeyRotation": "generate-new-deploy-all-prove-delete-old",
    },
    "targetApplication": {
        "configurationOwner": "deployment",
        "requiredFields": ["appId", "clientId", "slug"],
        "clientIdEnvironmentVariable": "CI_COORDINATOR_GITHUB_APP_CLIENT_ID",
        "clientIdPattern": "[A-Za-z0-9._-]{1,255}",
        "public": True,
    },
    "reviewerStepUpOAuth": {
        "authorizationEndpoint": "https://github.com/login/oauth/authorize",
        "clientCredentialKind": "github-app-client-secret",
        "clientSecretCustodyOwner": "deployment",
        "clientSecretRotation": "generate-new-deploy-all-prove-revoke-old",
        "pkceMethod": "S256",
        "tokenEndpoint": "https://github.com/login/oauth/access_token",
    },
    "repositorySelectionDefault": "selected",
    "currentPermissionProfile": {
        "profileId": "current-read",
        "permissions": {
            "actions": "read",
            "administration": "read",
            "checks": "read",
            "contents": "read",
            "merge_queues": "read",
            "metadata": "read",
            "organization_self_hosted_runners": "read",
            "pull_requests": "read",
        },
        "events": ["merge_group", "pull_request", "push", "workflow_run"],
    },
    "futurePermissionProfiles": [
        {
            "profileId": "governance-remediation",
            "status": "deferred",
            "permissions": {"administration": "write"},
            "blockingPrerequisite": (
                "Owner-approved desired state, remote-effect fencing, reconciliation, "
                "and rollback evidence."
            ),
        },
        {
            "profileId": "pr-report-publication",
            "status": "deferred",
            "permissions": {"checks": "write"},
            "blockingPrerequisite": ("Exact report identity and idempotent reconciliation."),
        },
        {
            "profileId": "recommendation-pull-request",
            "status": "deferred",
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "workflows": "write",
            },
            "blockingPrerequisite": ("Repository-owner opt-in and exact generated-byte review."),
        },
        {
            "profileId": "workflow-dispatch",
            "status": "deferred",
            "permissions": {"actions": "write"},
            "blockingPrerequisite": (
                "Registered block identity, remote-effect fencing, and timeout fallback."
            ),
        },
    ],
}

_EXPECTED_ATTESTATION: dict[str, object] = {
    "independentFromKeycloak": True,
    "authority": "ephemeral-github-step-up-receipt",
    "requiredBindings": [
        "expires-at",
        "initiating-actor",
        "issued-at",
        "proposal-digest",
        "provider-observation",
        "repository",
        "review-state",
        "reviewer-id",
        "reviewer-permission",
        "revision",
    ],
    "stepUp": {
        "activationPermissionRecheck": "github-app-current-reviewer-permission",
        "callbackRouteRef": "repositoryAttestationCallback",
        "flow": "github-app-user-authorization-code",
        "receiptMaximumSeconds": 300,
        "retainUserAccessToken": False,
        "startRouteRef": "repositoryAttestationStart",
        "transactionMaximumSeconds": 300,
    },
}

_EXPECTED_OBSERVATIONS: dict[str, object] = {
    "authorityState": "unconfigured",
    "observationOwner": "deployment",
}


def validate_provider_profile(
    github_app_value: object,
    attestation_value: object,
    observations_value: object,
    *,
    admitted_credential_kinds: set[str],
    admitted_routes: Mapping[str, object],
) -> None:
    github_app = as_object(github_app_value, "GitHub App profile")
    require_exact_json_value(github_app, _EXPECTED_GITHUB_APP, "GitHub App profile")

    attestation = as_object(attestation_value, "repository attestation")
    require_exact_json_value(
        attestation,
        _EXPECTED_ATTESTATION,
        "repository attestation",
    )
    app_authentication = as_object(
        github_app.get("appAuthentication"),
        "GitHub App authentication profile",
    )
    for field in (
        "installationTokenCredentialKind",
        "jwtCredentialKind",
        "privateKeyCredentialKind",
    ):
        if app_authentication.get(field) not in admitted_credential_kinds:
            raise ValueError("GitHub App authentication uses an unadmitted credential")
    oauth = as_object(github_app.get("reviewerStepUpOAuth"), "reviewer OAuth profile")
    if oauth.get("clientCredentialKind") not in admitted_credential_kinds:
        raise ValueError("reviewer OAuth uses an unadmitted provider credential")
    step_up = as_object(attestation.get("stepUp"), "repository attestation step-up")
    for field in ("callbackRouteRef", "startRouteRef"):
        route_ref = step_up.get(field)
        if not isinstance(route_ref, str) or route_ref not in admitted_routes:
            raise ValueError("repository attestation references an unadmitted route")

    observations = as_object(observations_value, "control-plane observations")
    require_exact_json_value(
        observations,
        _EXPECTED_OBSERVATIONS,
        "development provider observations",
    )
