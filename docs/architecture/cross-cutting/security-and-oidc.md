# Security And OIDC Specification

Status: cross-cutting specification

Date: 2026-09-02

## 1. Owned Invariant

No untrusted actor, workflow, fork context, agent output, or UI request can
obtain privileged credentials or selected execution authority without explicit
policy and identity proof.

## 2. Identity Predicates

### 2.1 GitHub Actions Plan Request

```text
ValidPlanRequest =
  issuer = https://token.actions.githubusercontent.com
  and audience = expected_audience
  and repository = expected_repository
  and repository_id = expected_repository_id
  and ref = event_ref
  and run_id = workflow_run_id
  and run_attempt = workflow_run_attempt
  and event_name = expected_event
  and (
    workflow_ref in allowed_target_workflow_refs
    or (
      RepositoryWorkflowPath(workflow_ref) in allowed_target_workflow_paths
      and ImmutableGitObject(workflow_sha)
    )
  )
  and (
    job_workflow_ref in allowed_reusable_workflow_refs
    or (
      RepositoryWorkflowPath(job_workflow_ref)
        in allowed_reusable_workflow_paths
      and ImmutableGitObject(job_workflow_sha)
    )
  )
  and workflow_sha = expected_workflow_sha where configured
  and job_workflow_sha = expected_job_workflow_sha where configured
```

If this predicate is false, selected CI is not allowed.
`workflow_ref` and `job_workflow_ref` are separate claim namespaces with
separate exact-ref and repository-workflow-path allowlists. Alternatives are
disjunctive only inside one namespace; configured namespaces are conjunctive.
Membership in one allowlist never authorizes another claim or identity mode,
even when the claim text is identical. Path admission establishes only a
trusted run identity.
Selected execution additionally requires the exact-revision adapter snapshot
and production subject defined by `REQ-CI-RUNTIME-027`, plus exact equality of
the registry-bound immutable requester ref and OIDC `job_workflow_ref` and
`job_workflow_sha`; a path match alone cannot authorize omission.

The privileged requester executes no target checkout or target-controlled
command. Its exact plan URL is also the OIDC audience, redirects are rejected,
and the token is never emitted as a workflow output. Target-controlled bytes
receive only bounded plan chunks or a bounded fallback reason.

### 2.2 Control-Plane Repository Action

```text
ValidControlPlaneRead(actor, scope) =
  UnexpiredKeycloakPrincipal(actor)
  and HasReadRole(actor)
  and ScopeAllowlisted(scope)

ValidRepositoryAttestation(actor, proposal) =
  KeycloakHuman(actor)
  and HasConfigureRole(actor)
  and ExactConfiguredOrigin
  and SessionDerivedCsrfProof
  and OneUseProposalBoundReviewerTransaction
  and FreshGitHubAppInstallationRelation(proposal.scope)
  and FreshReviewerPermission(proposal.scope, maintain or admin)

ValidActivation(actor, proposal) =
  ValidControlPlaneMutation(actor, proposal.scope)
  and ExactUnexpiredRepositoryAttestation(proposal)
  and CurrentReviewerPermissionRecheck
  and CurrentActiveBaselineMatchesReviewedBaseline
```

Keycloak OAuth state, PKCE, exact callback binding, ID-token admission, and an
opaque server-side session establish control-plane identity. They do not
establish repository ownership. A separate one-use GitHub reviewer flow binds
an immutable reviewer and fresh permission evidence to one exact proposal; its
user token is discarded after callback admission. A cookie, public repository,
Keycloak role, break-glass bearer, or one side of the reviewer/App relation is
insufficient. The exact protocol is owned by
[Control-Plane Identity And Repository Attestation](../modules/control-plane-identity-and-repository-attestation.md).

### 2.3 Organization Administration

The as-built contract separates Keycloak human and workload identity, Keycloak
client authentication, GitHub App provider authority, repository-owner
attestation, target workflow identity, webhook provenance, and emergency safety
authority. Its exact profile and operation matrix are owned by
[Organization Control Plane](organization-control-plane.md).

For any admitted operation `o`, caller credential `c`, and provider credential
`e` when a downstream provider effect is required:

```text
CallerAuthorized(c, o)
  => ExactTransport(o)
     and ValidCallerCredential(c, o)
     and ImmutablePrincipal(c)
     and ExactCallerRoles(c, o)
     and ExactCallerScope(c, o)
     and FreshCallerEvidence(c, o)

Authorized(c, o)
  => CallerAuthorized(c, o)
     and CapabilityOwnerAdmission(o)
     and ExactProviderEffectMode(o)
     and (NoProviderEffect(o) or ExactProviderEffectCredential(e, o))
     and (NoProviderEvidenceInput(o) or ExactFreshScopeBoundReceipt(o))

Every credential instance belongs to exactly one credential plane.
```

An operation whose provider-effect mode is forbidden cannot perform remote I/O.
In particular, validation may consume a current read receipt but cannot refresh
provider evidence itself.

## 3. Credential Laws

```text
AgentAdvice cannot introduce credential scopes.
RunnerCapacity cannot introduce credential scopes.
UI cannot introduce credential scopes.
CredentialPlane(target-workflow) != CredentialPlane(keycloak-control-plane)
CredentialPlane(keycloak-control-plane) != CredentialPlane(repository-attestation)
CredentialPlane(repository-attestation) != CredentialPlane(github-app)
CredentialPlane(github-app) != CredentialPlane(emergency-safety)
For every credential c: CredentialPlane(c) is total and single-valued.
SelectedCheckCredentialScope must be policy-approved.
Fork or untrusted actor context cannot receive privileged credentials.
Unknown actor trust => credential scope none or FullCI without privileged secrets.
```

## 4. Signature Laws

```text
verify(sign(payload, key), public_key) = true
payload mutation invalidates signature
expired envelope cannot start selected execution
wrong key id cannot start selected execution
wrong repository or run identity cannot start selected execution
```

## 5. Secret Handling

- logs must redact secret-like values.
- audit events may store credential profile ids, not secret values.
- reusable workflow secrets are explicitly named and policy-approved.
- `secrets: inherit` is forbidden by default.
- environment secrets are treated as a separate risk surface.
- Keycloak access, ID, and refresh tokens and GitHub reviewer tokens are never
  persisted or returned to the browser, OpenAPI, audit events, or logs.
- OAuth transactions, opaque session handles, reviewer transactions, and CSRF
  proofs are domain-separated; key replacement intentionally invalidates
  retained sessions and pending transactions.
- the Keycloak browser boundary persists only bounded identity, role, profile,
  issue, expiry, logout, and display facts behind an opaque handle.

### 5.1 Outbound Proxy Trust

The optional outbound proxy is a deployment-owned network intermediary, not an
application credential or authorization plane. Its URL is credential-free and
admitted only as explicit immutable process wiring; ambient proxy and CA
environment variables are ignored.

For the supported HTTP CONNECT route, the proxy can observe destination,
timing, and traffic volume while TLS terminates at GitHub. If the deployment
adds a trusted interception CA, that intermediary can observe or modify App
JWTs, installation and ephemeral reviewer tokens, OAuth codes, client secrets, provider
responses, and JWKS integrity. Such interception is therefore a distinct
high-authority security decision and is not admitted by the proxy URL setting.
`verify=False` is forbidden. Explicit custom CA support, if required later,
must have its own versioned trust-source, custody, rotation, and falsification
contract.

`trust_env=False` also means `SSL_CERT_FILE` and `SSL_CERT_DIR` are not trust
inputs. The current clients use the dependency's default verified trust store.

## 6. Failure Behavior

```text
InvalidOIDC => no selected CI
InvalidCredential => redacted HTTP 401
ForbiddenIdentity or claim policy mismatch => redacted HTTP 403
InvalidControlPlaneSession => bounded HTTP 401 without provider work
InvalidOrigin or CsrfProof => bounded HTTP 403 without review work
UnavailableRepositoryAttestationEvidence => bounded HTTP 503, never empty success
OIDC dependency unavailable => redacted HTTP 503
UnknownActorTrust => no privileged credentials
SignatureFailure => FullCI fallback
SignerUnavailable => explicit failure if local FullCI fallback cannot proceed
CredentialPolicyConflict => FullCI or blocked state
```

## 7. Required Tests

- OIDC mismatch matrix.
- exact-ref and revision-bound workflow-path namespace tests.
- signed envelope tamper tests.
- fork and actor trust tests.
- credential profile rejection tests.
- log redaction tests.
- Keycloak and reviewer OAuth state/PKCE/callback, session expiry/logout,
  Origin/CSRF, role, receipt binding, and repository-permission falsifiers.
