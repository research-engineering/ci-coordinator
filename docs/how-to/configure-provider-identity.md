# Configure GitHub App And Keycloak Identity

This is the provider setup for [container deployment](deploy-container.md), not
a provider-provisioning script or production qualification. Use only systems
you are authorized to administer. Examples use reserved hostnames; replace them
with deployment-owned values. No credentials belong in Git, browser settings
or copied CI fixtures.

GitHub App credentials are required for connected operation. Keycloak is
optional for the runtime, but required for normal authenticated workbench and
administrator API use. Omitting the entire identity block keeps those routes
closed; break-glass is not an alternative administrator login. Local `dev:up`
does not create these external authorities.

## 1. Fix The Public Addresses

Choose one canonical `CI_COORDINATOR_PUBLIC_ORIGIN`, for example
`https://coordinator.example.test`, with no trailing slash, path, query, userinfo
or explicit default port. Use that same origin in these provider settings:

| Provider setting | Exact URL relative to the public origin |
| --- | --- |
| GitHub App webhook | `/webhooks/github` |
| GitHub App user authorization callback | `/api/v1/repository-attestations/github/callback` |
| Keycloak browser valid redirect URI | `/api/v1/auth/keycloak/callback` |
| Keycloak valid post-logout redirect URI | `/workbench` |
| Keycloak back-channel logout URL | `/api/v1/auth/keycloak/backchannel-logout` |

Use full exact URLs, not wildcard callbacks or a GitHub App setup URL in place
of its user authorization callback. The edge must terminate trusted TLS and
forward webhook/logout bodies and headers unchanged, without interactive SSO
redirects on those provider routes. Forwarded headers do not establish identity.
Loopback HTTP is admitted only for the coordinator's local browser origin;
the Keycloak issuer still requires canonical HTTPS, trusted by the backend,
and must be reachable from both backend and browser.

## 2. Register And Install The GitHub App

In the intended organization's developer settings, follow GitHub's
[App registration procedure](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app):

1. Select the deployment-owned App name/homepage and installation audience.
   Enable the webhook at the URL above, keep SSL verification enabled, and
   provision a separate webhook secret.
2. Select the read permissions and event subscriptions for the intended
   capabilities from the
   [deployment permission table](deploy-container.md#7-connect-a-target-repository).
   The complete read profile includes repository Actions, Administration, Checks,
   Contents, Merge queues, Metadata and Pull requests, plus organization Self-hosted
   runners; the capacity-specific permissions are explained below. Do not
   grant write permissions: the current service does not own provider writes.
3. Add the exact user authorization callback above for proposal reviewer
   step-up. Keep **Expire user authorization tokens** enabled; the exchange
   requires a bounded `expires_in`. Do not enable device flow or request a new
   login on installation as a substitute for proposal-bound step-up.
4. Generate the App RSA private key and OAuth client secret into your secret
   manager. Record App ID separately from Client ID. Install the App on the
   intended organization and select the repositories it may access.
5. Supply `CI_COORDINATOR_GITHUB_APP_ID`, the downloaded PEM through
   `CI_COORDINATOR_GITHUB_PRIVATE_KEY_FILE`, and the matching
   `CI_COORDINATOR_WEBHOOK_SECRET_FILE`. With Keycloak enabled, also supply
   `CI_COORDINATOR_GITHUB_APP_CLIENT_ID` and
   `CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET_FILE` for that same App.

GitHub event subscriptions are event-wide. The runtime admits only the actions
listed in the deployment guide; a successful App registration or ping delivery
does not prove ingestion of a planning event. Subscription requirements follow
GitHub's [webhook event reference](https://docs.github.com/en/webhooks/webhook-events-and-payloads).

The runner permissions serve capacity observation, not administrator identity.
Repository runner reads need [Administration: read](https://docs.github.com/en/rest/actions/self-hosted-runners#list-self-hosted-runners-for-a-repository);
organization group/membership reads need
[Self-hosted runners: read](https://docs.github.com/en/rest/actions/self-hosted-runner-groups).
A deployment that does not admit this optional optimization must not claim
runner capacity evidence: denied or incomplete inventory takes the conservative
path, not a wider permission grant.

## 3. Configure The Keycloak Clients And Roles

Use a deployment-owned realm with an exact HTTPS issuer such as
`https://identity.example.test/realms/coordinator`, without a trailing slash.
Discovery, authorization/token/logout endpoints and JWKS must satisfy the
fixed issuer/origin profile. Configure RS256 signing with a resolvable `kid`.
Use the current Keycloak [client and role administration guide](https://www.keycloak.org/docs/latest/server_admin/index.html)
for its UI; the required coordinator values below are not arbitrary aliases.

1. Create OIDC resource client **`ci-coordinator-admin-api`**, with no login or
   service-account flow needed for this resource client. Under its client roles,
   define `read`, `configure`, `activate`, `override`, and `audit`.
2. Create confidential OIDC browser client **`ci-coordinator-admin-ui`** with
   client authentication, Standard Flow, PKCE **S256**, and
   `client_secret_basic` code exchange. Leave implicit flow, direct access grants
   and service accounts disabled for this browser client. Register the exact
   redirect and post-logout URLs above. Do not suppress the `iss` authorization
   response parameter; the callback requires it to equal the configured issuer.
3. Add a **User Client Role** protocol mapper for `ci-coordinator-admin-api`
   producing the multivalued claim
   `resource_access.ci-coordinator-admin-api.roles` in the **ID token**.
   Make the mapper directly effective or part of a default client scope: the
   coordinator requests only `openid`, not an optional role scope. Do not rely
   on realm roles or the default access-token-only mapper.
4. Assign only the required API client roles to approved users/groups and admit
   those roles through client scope mappings. `read` opens the workbench;
   `configure` permits configuration operations; `activate` is separate;
   `override` permits emergency validation controls; `audit` permits audit
   operations. Enable omission requires both `activate` and `override`.
   Proposal review additionally needs the human's current GitHub `maintain` or
   `admin` permission, independently of Keycloak roles.
5. Configure the browser client's back-channel logout URL, include session ID,
   and leave offline-session revocation extensions off for this profile. The
   coordinator uses OIDC back-channel logout, not a Keycloak adapter Admin URL
   or front-channel logout. Register `/workbench` for the post-logout redirect.

Before accepting this configuration, inspect a provider-issued ID token through
an authorized, redacted validation procedure: `aud` must contain **only**
`ci-coordinator-admin-ui`, `azp` must equal that client, and nonce, subject,
`sid`, `iat`, `exp` and the API role array must be present. Only the five role
names above are admitted in that array, with no duplicates. Adding an API
audience to the browser ID token would cause rejection, not grant API access.
ID-token expiry caps the opaque session; the configured session maximum is
60--900 seconds. Access/refresh tokens do not become a browser refresh session.

Do not import `docker/ci/keycloak-realm.json` into production. It is a controlled
CI fixture with a synthetic user, a subset of roles and incomplete production
logout wiring, not an approved realm export.

## 4. Supply The Identity Block And Scope

Add the complete block to the deployment env file alongside the connected
settings from [`.env.example`](../../.env.example):

```dotenv
CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE=keycloak
CI_COORDINATOR_KEYCLOAK_ISSUER=https://identity.example.test/realms/coordinator
CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID=ci-coordinator-admin-ui
CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET_FILE=/run/secrets/keycloak-browser-client-secret
CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID=ci-coordinator-admin-api
CI_COORDINATOR_PUBLIC_ORIGIN=https://coordinator.example.test
CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY_FILE=/run/secrets/control-plane-session-key
CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS=900
CI_COORDINATOR_KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST=
CI_COORDINATOR_GITHUB_APP_CLIENT_ID=<this-app-client-id>
CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET_FILE=/run/secrets/github-app-client-secret
```

Provision an independent random 32-byte session key encoded as canonical
unpadded base64url (43 characters). It, the browser client secret and the GitHub
OAuth secret must be distinct. Follow the
[file-secret constraints and mounts](deploy-container.md#mount-secret-files),
then restart the owned service. Never pass the secret values to the frontend.

Keep inventory and command authorization separate. In default restricted mode,
set `CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST` to the
intended installation IDs and `CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST` to
the intended `installationId:repositoryId` pairs. Use canonical comma-separated
values as admitted by the runtime. App-wide inventory and App scope mode are
explicit alternatives in [installation discovery](discover-installed-organizations.md),
not automatic onboarding defaults or substitutes for role checks.

For optional machine API access, create a separate confidential workload client
with service accounts/client credentials, no browser flow, and only the required
API client roles. Its access token must include audience `ci-coordinator-admin-api`,
the same API role claim, stable subject and its own client ID as `azp`. Configure
the role and audience mappers for the **access token**, not the browser ID token;
keep its positive `exp - iat` lifetime at most 300 seconds. Add the exact workload
client ID to the sorted, unique `CI_COORDINATOR_KEYCLOAK_WORKLOAD_CLIENT_ALLOWLIST`
and restart. An empty allowlist admits no workloads. Only the issued access
token is sent as bearer; never send the workload client secret to the coordinator.

## 5. Validate The Connected Path

After the [deployment health/readiness checks](deploy-container.md#5-probe-and-admit-the-instance),
open `/workbench`, complete Keycloak sign-in, and inspect `/api/v1/auth/session`
for the intended actor, roles and expiry. Confirm the authorized repository is
visible and a different, ungranted scope remains denied. Complete
[proposal review](review-workflow-proposal.md) only for an approved proposal;
Keycloak login alone is not repository-manager attestation or activation.

Exercise normal logout and an independently initiated Keycloak logout against
the exact deployed version; prove the old opaque session loses access. The
current back-channel route expects one form `logout_token` and exactly
`Content-Type: application/x-www-form-urlencoded` without parameters. Its
signed logout-token profile requires the browser audience, `logout+jwt` header,
`Logout` claim type, the logout event, `jti`, `iat`, `exp` (lifetime at most
120 seconds), and `sid` or `sub`. An incompatible provider delivery is an
admission blocker, not evidence that revocation works; do not weaken validation
or label the CI fixture a live logout proof.

Retain redacted exact-version login, denied-role/scope, App event, logout and
optional workload evidence. Startup/JWKS readiness alone proves none of these
end-to-end effects or [production admission](../architecture/cross-cutting/production-admission.md).
The [identity owner](../architecture/modules/control-plane-identity-and-repository-attestation.md)
and [HTTP reference](../reference/http-surface.md) define the authority boundaries.
