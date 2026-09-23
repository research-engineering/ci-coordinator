# Identity Admission Module Specification

Status: module specification

Date: 2026-07-08

## 1. Owned Invariant

Untrusted GitHub webhook or GitHub Actions OIDC input becomes trusted runtime
identity only after cryptographic and claim-bound verification.

## 2. Public API

```text
verify_webhook(headers, raw_body, secret, clock) -> TrustedWebhook | RejectedIdentity
admit_verified_actions_oidc_claims(claims, expected, clock) -> TrustedActionsRun | RejectedIdentity
verify_actions_oidc(jwt, expected) -> TrustedActionsRun | RejectedIdentity
normalize_run_identity(event_context, oidc_claims) -> WorkflowRunIdentity
```

`admit_verified_actions_oidc_claims` is a pure predicate over already
cryptographically verified GitHub Actions OIDC claims. It does not parse JWTs,
load JWKS, verify RSA signatures, read environment configuration, or perform
network I/O. `verify_actions_oidc` is the later adapter that must compose JWT
signature verification with this claim predicate before production use.

The adapter accepts a bounded, caller-fetched JWK snapshot and admits exactly
an `RS256` compact JWT with `alg`, `kid`, and an optional `typ=JWT` header.
It never follows token-supplied key URLs. The selected JWK must be a unique
`RSA`, `use=sig`, `alg=RS256` verification key projected only from recognized
public verification members. Unknown provider metadata is discarded before
selection. Signature verification occurs before claim admission; temporal
claims are evaluated only by the injected clock in the pure predicate.
Therefore:

```text
TrustedActionsRun(token) =>
  RS256HeaderAdmitted(token)
  and UniqueAdmittedJwkSelected(token.kid)
  and SignatureValid(token, selectedJwk)
  and ClaimsAdmitted(token.claims, expected, injectedClock)
```

JWK retrieval, caching, refresh, and provider availability are separate
adapter concerns. Their failure cannot be represented as cryptographic success.

## 3. Inputs And Outputs

Inputs:

- raw webhook body and signature headers.
- GitHub Actions OIDC JWT.
- expected repository, ref, run id, run attempt, direct workflow identity,
  reusable job workflow identity, optional repository workflow-path
  identities, and audience.
- optional expected workflow and reusable job workflow SHA bindings.

Outputs:

- trusted repository identity.
- trusted workflow run identity.
- rejected identity with reason code.

## 4. Input Completeness Rules

OIDC verification requires the full expected claim set:

```text
repository
repository_id
ref
run_id
run_attempt
event_name
workflow identity
audience
issuer
expiration
```

Where a trusted caller configures expected workflow SHA bindings, the admitted
claims must match those SHA values. Exact-ref admission does not infer an
additional SHA constraint. Path admission always requires the corresponding
`workflow_sha` or `job_workflow_sha` to be a canonical immutable Git object id;
the runtime then verifies the complete target adapter at that revision before
selected execution can be projected.

Webhook verification requires the raw body bytes and signature headers before
any trusted parsing. Duplicate webhook signature header values are ambiguous
and rejected before payload trust. The signature field must be exactly
`sha256=` followed by 64 lowercase ASCII hexadecimal characters. Only after
that bounded shape admission does verification perform a constant-time byte
comparison. If any expected claim is absent, ambiguous, stale, or not bound to
the request, the module returns `RejectedIdentity`.

## 5. Failure Behavior

```text
InvalidWebhookSignature => reject before parsing trusted payload
InvalidOIDC => no selected CI, FullCI fallback where bootstrap can run it
ClaimMismatch => no selected CI
UnknownWorkflowIdentity => no selected CI
ExpiredToken => no selected CI
Missing required claim => corresponding ordered field mismatch reason, no selected CI
```

The HTTP adapter classifies malformed, invalid, or temporally invalid
credentials separately from cryptographically verified identities that fail the
claim policy. JWKS retrieval and key-provider failures remain dependency
unavailability. None of these transport projections exposes the internal
`RejectedIdentity` reason or message in a response body.

## 6. Private Boundary

This module must not decide which checks are required, whether a check can be
omitted, or which credentials a selected check receives.

It owns only admission from untrusted GitHub/webhook/OIDC data into trusted
identity records. Plan issuance, planning semantics, persistence, and workflow
matrix construction consume the admitted identity instead of re-verifying it.

## 7. Forbidden Imports

```text
dotenv
fastapi routers
os
sqlalchemy
planning_core
verification_core
plan_issuance
execution_orchestration
runner_capacity
```

## 8. Audit And Replay Facts

Trusted and rejected identity records must contain stable reason codes,
repository identity, run identity when known, claim hash, verifier version, and
verification time from an injected clock. They must not persist raw JWTs,
webhook secrets, or signature material.

## 9. Proof Obligations

| Obligation                                         | Falsifier                                                                                                               |
|----------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Wrong repository claim is rejected.                | JWT for repo A requests plan for repo B.                                                                                |
| Wrong ref is rejected.                             | Token bound to one ref signs plan for another ref.                                                                      |
| Wrong run attempt is rejected.                     | Stale attempt requests selected plan.                                                                                   |
| Workflow allowlist is enforced.                    | A non-allowlisted target workflow obtains a selected plan.                                                              |
| Workflow claim namespaces remain separate.         | A `job_workflow_ref` is admitted only because its text appears in the `workflow_ref` allowlist.                         |
| Target workflow is the authenticated run workflow. | A registry path different from the top-level `workflow_ref` path obtains selected execution or fallback reconciliation. |
| Expired tokens are rejected.                       | Expired JWT creates trusted identity.                                                                                   |

## 10. Implementation Mapping

Target files:

```text
ci_coordinator/identity_admission/webhook_signature.py
ci_coordinator/identity_admission/actions_oidc.py
ci_coordinator/identity_admission/repository.py
ci_coordinator/identity_admission/workflow_run.py
ci_coordinator/identity_admission/claims.py
```

## 11. Acceptance Tests

- webhook signature positive and negative fixtures.
- verified OIDC claim predicate positive and negative fixtures.
- OIDC claim mismatch matrix.
- expired token rejection.
- workflow identity allowlist tests.
- no selected plan can be issued from rejected identity.
- audit fact redaction tests for raw JWT, secrets, and signatures.
