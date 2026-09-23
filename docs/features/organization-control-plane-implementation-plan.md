# Organization Control Plane Implementation Plan

Status: approved sequence; Batch A contract

Date: 2026-09-02

Owner: `ci-coordinator.control-plane`

## 1. Goal

Replace the pre-release GitHub-login administrator surface with the
[organization control-plane contract](../architecture/cross-cutting/organization-control-plane.md),
then expose complete repository configuration capabilities through one API
used by browser and machine clients.

The implementation is complete only when:

```text
Complete =
  ContractFrozen
  and KeycloakHumanIdentity
  and KeycloakMachineIdentity
  and ExactGitHubAppIdentity
  and RepositoryAttestationPreserved
  and ApiFirstConfigLifecycle
  and UiParity
  and LiveExternalReceipts
  and LegacyAuthorityRemoved
```

### 1.1 Declared Product Changes

| Capability                    | As-built authority                                          | First-release target                                                                 |
|-------------------------------|-------------------------------------------------------------|--------------------------------------------------------------------------------------|
| administrator login and reads | GitHub user-and-App intersection                            | Keycloak fine-grained role intersected with exact App installation scope             |
| repository attestation        | persisted GitHub user-token session plus fresh manager read | proposal-bound ephemeral GitHub step-up, bounded receipt, and App permission recheck |
| administrative API            | broad deployment bearer plus current browser subsets        | Keycloak human/workload principals with exact per-operation roles                    |
| emergency access              | broad deployment bearer                                     | safety-increasing FullCI and omission-disable commands only                          |

These are intentional pre-release product changes. They are not compatibility
refactors and cannot be introduced implicitly by an adapter replacement.

## 2. Sequencing Proof

External Keycloak clients and GitHub App settings require exact callbacks,
roles, permissions, and actor semantics. Runtime code requires the same facts.
Therefore both depend on one frozen profile. API and UI authority depend on the
identity implementation, while the UI additionally depends on stable API
schemas. Live deployment depends on all local contracts and artifacts.

```text
profile
  -> identity runtime
  -> API lifecycle
  -> UI projection
  -> external wiring
  -> non-enforcing exercise
  -> legacy removal and production admission
```

Any order that places a dependent node first either duplicates policy or
requires semantic rework. This partial order is therefore minimal for the
declared constraints; independent tests and documentation may proceed in
parallel within a node.

## 3. Batch A: Contract And Machine Profile

Deliverables:

- canonical cross-cutting control-plane contract;
- this implementation plan;
- `control-plane-profile.v1.json` with finite planes, roles, fixed
  trust-boundary routes,
  permissions, events, session bounds, attribution, and transition policy;
- one active profile-consistency requirement and deferred runtime
  requirements;
- semantic profile falsifiers;
- architecture index, traceability, roadmap, and Proofkit route closure.

Acceptance:

- all authority planes and role sets are pairwise explicit;
- caller alternatives and downstream provider-effect credentials are modelled
  independently;
- every provider effect is exactly forbidden or required under one exact
  permission profile, while optional provider facts enter pure operations only
  through a fresh scope-bound receipt;
- no profile field can imply repository-owner consent from Keycloak;
- the selected target GitHub App identity is normative while its live
  observation remains non-authorizing;
- current and future GitHub permission profiles are distinct;
- the profile admits exactly one active break-glass action set;
- live observations remain labelled observations, not readiness claims; and
- Proofkit admits the source and exact compact route projection.

This batch changes no runtime route or credential behavior.

Static proof ownership is deliberately narrow:

| File                                          | Sole responsibility                                             |
|-----------------------------------------------|-----------------------------------------------------------------|
| `scripts/control_plane_profile.py`            | bounded loading, exact plane projection, and global composition |
| `scripts/control_plane_identity_profile.py`   | exact Keycloak and immutable-actor projection                   |
| `scripts/control_plane_operation_profile.py`  | exact per-plane caller and downstream provider-profile algebra  |
| `scripts/control_plane_provider_profile.py`   | exact GitHub App, attestation, and observation projection       |
| `scripts/tests/test_control_plane_profile.py` | single-component semantic falsifiers through the public loader  |

The section validators are not runtime identity abstractions. They exist
because identity, caller-operation, and provider profiles change independently,
while the top-level loader retains the global disjointness and admitted-set
composition proof.

## 4. Batch B: Identity Runtime

### 4.1 Package Ownership

Create:

```text
control_plane_identity/
  model.py          immutable principals, sessions, roles, outcomes
  ports.py          discovery, token exchange, JWKS, session store
  crypto.py         transaction/session handles and CSRF derivation
  browser.py        authorization-code and session use cases
  machine.py        access-token admission and role projection

integrations/keycloak/
  discovery.py      bounded exact-issuer metadata
  client.py         code exchange and logout transport
  jwks.py           bounded cache and unknown-kid refresh
  tokens.py         ID, access, and logout-token verification

persistence/
  _schema_control_plane_sessions.py
  control_plane_session_repository.py
  control_plane_session_schema_attestation.py

api/http/
  control_plane_authentication.py
  routers/control_plane_identity.py
```

`control_plane_identity` owns trusted values and protocol-independent state
transitions. `integrations/keycloak` owns remote OIDC mechanics. Persistence
owns SQL only. HTTP owns cookies, redirects, headers, and response projection.
Runtime composition is the only concrete wiring owner.

Do not create a generic identity-provider framework. There is one admitted
provider and no second behaviorally substitutable implementation.

### 4.2 Dependency Selection

As of 2026-09-02, the implementation candidates are `Authlib==1.8.0` for the
OAuth/OIDC client protocol and `joserfc==1.7.5` for JOSE primitives. Both are
production-stable, Python 3.14-compatible releases published through PyPI
Trusted Publishing. Use `joserfc` directly rather than the deprecated
`authlib.jose` namespace.

Authlib 1.8.0 already requires `joserfc`. Because the repository currently uses
PyJWT for Actions OIDC and GitHub App JWTs, the implementation batch must run an
exact claim/header/error parity study. If the study passes, migrate those two
adapters to `joserfc` and remove PyJWT in the same dependency cutover; retaining
two JOSE engines without a proved semantic requirement is unjustified. If a
parity falsifier fails, keep PyJWT and record the exact behavioral reason rather
than forcing cosmetic dependency unification.

Do not add either dependency in this contract-only batch. Pin them in the
identity-runtime merge unit after API-surface inspection proves the required
features and native falsifiers cover algorithm allowlisting, key selection,
claim validation, token confusion, and exception redaction. Wrap only remote
protocol mechanics; domain admission remains project-owned.

- [Authlib 1.8.0](https://pypi.org/project/Authlib/1.8.0/)
- [joserfc 1.7.5](https://pypi.org/project/joserfc/1.7.5/)

### 4.3 Pre-Release Schema Replacement

Replace the initial `browser_sessions` relation with a final
`control_plane_sessions` relation. Retain no GitHub token columns or legacy
codec. Update schema capability, ACL, attestation, migration, and persistence
tests atomically.

Development deployments reset session and unactivated test state. No
production compatibility path is claimed.

### 4.4 Runtime Settings

Move identity settings out of the already broad runtime settings admission
module into a bounded identity profile parser. Admit exact issuer, browser and
API client ids, public origin, session key, session maximum, algorithm set,
workload client allowlist, and profile digest.

Secrets remain file-capable deployment inputs. Public projections expose only
presence, counts, ids safe for diagnostics, and profile digest.

### 4.5 Falsifiers

- wrong discovery issuer, endpoint origin, algorithm, `kid`, signature,
  audience, `azp`, nonce, subject, time, role path, or role value;
- Keycloak client credential accepted as an API bearer, callback exchange
  without exact `client_secret_basic`, or partial-replica secret rotation;
- stale discovery or JWKS cache and an overlong machine-token lifetime;
- missing, expired, or overlong back-channel logout-token `exp`;
- token-supplied key URL;
- duplicate callback parameter;
- access or refresh token persistence;
- session accepted after expiry, profile change, logout, or back-channel
  logout;
- logout token with wrong issuer, audience, event, time, `jti`, replay state,
  nonce presence, or `sid`/`sub` target;
- ID token accepted as machine bearer;
- browser cookie accepted as machine credential;
- mutation without exact Origin and CSRF;
- secret, token, code, verifier, or cookie in logs, errors, OpenAPI, frontend
  assets, or fixtures; and
- unbounded JWKS, discovery, token, or logout response.

## 5. Batch C: Principal And Capability Integration

Introduce tagged principals:

```text
KeycloakHumanPrincipal
KeycloakWorkloadPrincipal
GitHubReviewerPrincipal
BreakGlassPrincipal
```

Replace router-specific static/browser branching with one authentication
result and capability-owned role checks. The principal carries identity and
role evidence, not business permission. Capability owners retain scope, OCC,
and command admission.

Administrator reads use Keycloak plus the GitHub App. GitHub user OAuth is
reduced to reviewer step-up and cannot authenticate general UI or API routes.
The production break-glass principal is accepted only by `force_full_ci` and
`disable_omission`.

Reviewer step-up starts from an authenticated Keycloak mutation and binds the
exact session, repository, revision, proposal digest, state, and PKCE verifier.
Its callback resolves fresh GitHub identity and manager evidence, reproduces the
proposal, writes one bounded receipt plus audit event, and discards the user
token. Activation rechecks the recorded reviewer identity's current repository
permission with the exact App profile. Do not retain a second OAuth-backed
browser session.

Provider readiness adds exact App owner/id and installation-profile checks.
No provider write route is added in this batch.

Falsifiers cover a changed Keycloak session, repository, revision, proposal
digest, OAuth state, reviewer id, role, installation, permission observation,
receipt time, or App recheck; callback replay; user-token persistence; and
activation after reviewer permission revocation.

## 6. Batch D: API-First Configuration Lifecycle

This batch implements `REQ-CI-CONTROL-008` and adds its exact finite
operation-to-method-and-path transport profile before any new UI projection.

Implement the smallest complete vertical capability for an already visible
repository:

1. `POST /api/v1/config/validations` performs pure admission and no write.
2. Configuration registration gains `operationId`, exact request identity,
   same-input replay, changed-input conflict, and atomic epoch plus audit.
3. Existing activation and rollback retain their semantic owners and gain the
   Keycloak principal adapter.
4. Repository-scoped status exposes bounded active and retained revision
   metadata.
5. Immutable source export returns exact retained bytes, digest, revision, and
   ETag without widening the workbench read model.

Use versioned Pydantic HTTP models as transport contracts. Domain records
remain dataclasses unless validation, trust, version, or lifecycle semantics
justify a separate DTO. Do not add repository or use-case classes merely for
symmetry.

Required proofs:

- dry-run and registration admission are byte-for-byte equivalent before the
  persistence effect;
- dry-run touches no store, audit, transaction, or clock effect;
- same operation and bytes produce one epoch and audit event;
- changed input under the same operation conflicts;
- concurrent commands produce at most one winning state transition;
- export reproduces exact retained bytes and fails closed on corruption; and
- cross-scope access is rejected before data projection.

## 7. Batch E: UI Projection

Generate TypeScript contracts from the exact in-process OpenAPI projection.
Create separate frontend API owners for identity and config lifecycle; do not
expand a generic client or schema file across capabilities.

The UI sequence is:

```text
Keycloak login
  -> authorized repository catalog
  -> exact workflow discovery
  -> deterministic proposal and validation
  -> repository-owner attestation when required
  -> registration
  -> explicit activation or rollback
  -> status and audit evidence
```

The frontend never reimplements policy validation. It preserves operation ids
across retries, treats `409` as stale evidence requiring refresh, aborts older
authority generations, and exposes no action for which the session lacks the
exact role.

Browser witnesses cover Chromium, Firefox, WebKit, keyboard operation,
responsive layouts, reduced motion, focus, contrast, stale responses, session
expiry, CSRF rejection, and all typed API outcomes.

## 8. Batch F: External Objects And Deployment

Keycloak owner actions:

- require Keycloak 26.7.3 or later with the admitted protocol profile;
- create `ci-coordinator-admin-ui` as a confidential code-flow client;
- restrict its token-endpoint authentication to `client_secret_basic`, custody
  the secret in deployment, and rotate it only through the bounded
  login/callback-ingress drain defined by the profile;
- create `ci-coordinator-admin-api` as the exact audience and role owner;
- create roles `read`, `configure`, `activate`, `override`, and `audit` plus
  assignment-only composite `administrator`;
- disable direct access grants, implicit flow, and Full Scope Allowed;
- register exact callback, post-logout, and back-channel logout URIs;
- map only admitted API roles into the browser ID token and machine access
  tokens; and
- create one service-account client per workload with an independently
  custodied client credential.

GitHub owner actions:

- admit one GitHub App with independently verified ownership;
- configure public distribution so separately consenting target organizations
  can install the same exact App;
- verify exact read permissions, events, reviewer callback, and webhook URLs;
- install only in target organizations and repository sets approved by their
  owners;
- place App private key, reviewer OAuth client secret, and webhook secret under
  deployment-owned rotation; rotate the private key by generating a new key,
  deploying and proving it on every replica, then deleting the old key; rotate
  the client secret by generating a new value, deploying and proving it on
  every replica, then revoking the old one; and
- do not grant a write profile before its runtime capability is admitted.

Deployment starts non-enforcing. Retain discovery, login/logout, back-channel
logout, machine token, App identity, installation inventory, webhook, target
OIDC, fallback, rotation, and actor-attribution receipts. No local test may
substitute for these external facts.

## 9. Batch G: Legacy Removal And Closeout

Delete GitHub-login administrator code, old session schema, settings,
documentation, frontend states, and tests only after the Keycloak and reviewer
step-up consumers are complete. Keep GitHub reviewer code only while it owns an
actual attestation path.

Run an independent closeout review over exact branch bytes. Required claims:

- every configured route has exactly one credential plane;
- every command has one role and capability owner;
- every provider effect retains its initiating actor;
- no obsolete GitHub-admin or broad static-bearer path remains;
- all generated artifacts match source;
- all Proofkit requirements are blocking only when their native witnesses
  exist; and
- exact-head Full Check, CodeQL, provider exercises, and rollback evidence are
  green.

Only then may the deferred requirements become blocking.

## 10. Non-Claims

This plan does not authorize external configuration, production deployment,
provider writes, repository-owner consent, or CI omission. It does not require
a generic IAM framework, refresh-token subsystem, service mesh, distributed
trace collector, or additional microservice.
