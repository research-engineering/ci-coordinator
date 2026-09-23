# Control-Plane Identity Authority Cutover Implementation Plan

Status: implementation complete locally; exact-head and external closure pending

Date: 2026-09-02

Owner: `ci-coordinator.control-plane`

Design: [Control-Plane Identity Authority Cutover](control-plane-identity-authority-cutover.md)

## Scope

Implement one authority cutover from retained GitHub-user administrator state
to Keycloak control-plane identity, GitHub reviewer step-up, GitHub App provider
authority, and a safety-only break-glass credential. Preserve existing read,
proposal, review, and activation product outcomes while narrowing authority.

## Owner Map

| Surface                                     | Owner                       | Required result                                        |
|---------------------------------------------|-----------------------------|--------------------------------------------------------|
| principals, sessions, browser state machine | `control_plane_identity`    | immutable typed authority and token-free state         |
| Keycloak and GitHub protocol mechanics      | `integrations`              | bounded transport with typed unavailability            |
| proposal receipt and activation admission   | `proposal_review` and `app` | exact replay, receipt, permission, and baseline checks |
| durable state and schema attestation        | `persistence`               | one local transaction per authority transition         |
| cookies, CSRF, status/body projection       | `api.http`                  | exact perimeter behavior without domain policy         |
| process settings and secret files           | `runtime_settings`          | fail-closed construction and redacted projection       |
| concrete allocation and lifecycle           | `runtime`                   | one composition owner and bounded cleanup              |
| browser state and commands                  | `frontend`                  | typed projection only; no authority invention          |
| local state migration                       | `scripts.dev_environment`   | resumable byte-preserving schema transition            |

## Atomic Sequence

1. Add exact Keycloak, reviewer, GitHub App, and break-glass settings and reject
   every mixed or incomplete credential plane.
2. Introduce tagged principals and token-free session values without a generic
   identity-provider abstraction.
3. Add bounded Keycloak discovery, token, JWKS, logout, and back-channel logout
   adapters; use `joserfc` as the production JOSE engine.
4. Replace browser-session persistence with final control-plane session,
   reviewer transaction, review receipt, schema capability, ACL, and attestation
   relations in the single pre-release initial migration.
5. Replace router-local authentication branches with one principal admission
   followed by capability-owned role and repository-scope checks.
6. Bind reviewer step-up to the exact session, operation, scope, proposal,
   expected active pointer, state, PKCE verifier, and expiry; discard its token.
7. Register the reviewed epoch without activation, then activate only after
   fresh reviewer permission and two complete active-pointer comparisons.
8. Project the exact OpenAPI contracts into separate identity, attestation, and
   activation frontend clients; remove predecessor GitHub administrator state.
9. Migrate local metadata to schema 3, preserve secret bytes and volumes, remove
   frontend secret mounts, and inject no development proxy credential.
10. Remove the predecessor code, tests, schema, settings, and generated
    projections in the same merge unit.

## Closeout Corrections

Independent review introduced no new capability and closed these bounded
counterexamples:

1. preserve the original reviewer operation id across OAuth only as an
   untrusted hint and require authenticated durable replay before authority is
   shown;
2. clear callback cookies at the outer user-middleware boundary for success,
   route rejection, rate rejection, deadline, correlation/observation failure,
   and redacted exception;
3. delete the exact pending reviewer transaction on every terminal and
   exception path, retaining bounded expiry only as cleanup-failure fallback;
4. compare the full active pointer before provider I/O and again under the final
   lock;
5. admit exactly one decoded `logout_token` form field;
6. delete the obsolete successor-migration oracle after folding the pre-release
   schema into the initial revision; and
7. make every config middleware response conform to the same error schema used
   by the generated browser client; and
8. construct the OpenAPI-only route projection with explicit composition-time
   callback-cookie settings rather than structurally invalid bare objects.
9. admit the PostgreSQL row-lock privilege required by reviewer-transaction
   registration as `UPDATE(handle_digest)` only, while a catalog-attested
   trigger rejects every actual control-plane session update; and
10. replace the predecessor administrator credential in the composition test
    with machine-administrator registration, while retaining activation proof
    in the dedicated proposal-authority integration witnesses; and
11. bind the ephemeral reviewer adapter to an owner-level coverage floor and
    exercise its complete OAuth, identity, repository, and permission outcome
    algebra through the public port; and
12. close the browser-identity risk floor with parameterized provider,
    persistence, temporal-evidence, session, and back-channel failure
    witnesses rather than weakening the admitted coverage policy.
13. require process-group absence and capture-pipe closure to agree before a
    proof command accepts residual quiescence, with a deterministic witness for
    one transiently incomplete process-table observation.
14. exercise every configured machine-identity policy bound through one
    parameterized negative oracle rather than weakening the security-owner
    branch floor.

## Proof Matrix

| Claim                           | Required falsifier                                                                                                        |
|---------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| credential planes are disjoint  | try every mixed header/cookie/token grammar and role combination                                                          |
| sessions retain no bearer       | inspect codecs, SQL columns, responses, logs, generated assets, and fixtures                                              |
| reviewer receipt is exact       | mutate each session, actor, scope, operation, manifest, baseline, state, PKCE, permission, and time operand independently |
| activation is stale-safe        | change any active-pointer field before provider I/O and during final-lock acquisition                                     |
| callback authority closes       | force every middleware, route, store, and provider terminal/exception path                                                |
| initial schema is final         | upgrade, downgrade, capability, ACL, checksum, and no-successor topology checks                                           |
| local migration is conservative | exercise every admitted prefix plus reversed, mixed, foreign, malformed, and future state                                 |
| frontend is only a projection   | contradict each status/body pair, abort each request, exceed byte bounds, and forge callback hints                        |
| architecture boundaries hold    | closed first-party import policy, owner ledger, and changed-candidate review                                              |

## Verification Order

1. Run static formatting, lint, type, import-boundary, documentation graph,
   architecture traceability, module-ownership, Proofkit admission, OpenAPI, and
   generated-contract gates against the final candidate.
2. Publish exactly one branch commit and run targeted and aggregate behavioral
   suites only through the repository-owned GitHub Actions route.
3. Admit Full Check only for the exact branch head and complete required matrix.
4. Run one independent frozen-tree review after all semantic repairs.
5. Squash-merge only the reviewed exact head; treat the merge commit and its
   post-merge Full Check as distinct evidence.
6. Obtain live Keycloak, GitHub App, reviewer callback, database, and deployment
   receipts before changing any status to production-ready.

## Completion Predicate

```text
LocalComplete := StaticGates
                 and ExactHeadFullCheck
                 and FrozenTreeReview

ExternallyReady := LocalComplete
                   and KeycloakReceipt
                   and GitHubAppReceipt
                   and ReviewerCallbackReceipt
                   and DatabaseReceipt
                   and DeploymentReceipt
```

The current merge unit may claim `LocalComplete` only after its exact-head
provider evidence exists. It cannot claim `ExternallyReady`, provider
enforcement, or safe omission from local or CI evidence alone.
