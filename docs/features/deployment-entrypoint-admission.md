# Deployment Entrypoint Admission

Status: design

Date: 2026-09-07

## Decision

Complete the two first-contact paths exposed by the deployed product: browser
navigation to the administrator origin and GitHub webhook connection testing.
This is one deployment-onboarding batch, not a new planning capability. Its
execution sequence is in the [implementation plan](deployment-entrypoint-admission-implementation-plan.md).

The current requirements remain the semantic owners:
`REQ-CI-UI-006` owns delivery of the operator shell; `REQ-CI-RUNTIME-001` owns
authenticated ingestion; `REQ-CI-RUNTIME-021` owns browser identity. This
document explains their narrowly expanded domains.
No database migration, new dependency, proxy rewrite, credentials, repository
registration, workflow change, or omission authority is introduced.

## Browser Navigation

When the verified UI bundle is mounted, `GET /` and `HEAD /` return a no-store
307 redirect to the fixed relative `/workbench`. The response retains the common
UI security headers. Query values, host values and forwarded headers never
participate in the target. Other methods and unknown paths do not serve the UI;
disabling the UI also removes the root redirect. API authentication is unchanged.

```text
UiMounted and method in {GET, HEAD} and path = /
  => status = 307 and Location = /workbench and no authority change
not UiMounted => no UI root route
```

A fixed application-owned redirect is preferable to an edge-only redirect
because the packaged service behaves consistently on every supported deployment.
A catch-all SPA route would conceal unknown API paths and is unnecessary. The
307 response is deliberately non-cacheable so later routing changes are not
pinned in browsers. Revisit only if the product adopts an owned base-path model.

## Keycloak Browser Admission

A counterexample admits only `code` and `state` in a Keycloak callback while
a supporting provider also sends `iss` and `session_state`. The application
must implement the admitted issuer-response contract rather than disable the
extension. [RFC 9207 section 2.4](https://www.rfc-editor.org/rfc/rfc9207.html#section-2.4)
requires exact issuer comparison and rejection of a missing issuer from a
supporting authorization server. This is not a retained deployment receipt.

A strict Pydantic query model admits exactly one bounded nonempty `code`, one
canonical `state`, one `iss` equal to the deployment issuer, and an optional
bounded nonempty `session_state`. Duplicate keys are rejected before mapping;
unknown keys reject under this closed Keycloak profile. `session_state` is
inert navigation metadata, never the signed ID-token `sid`. OpenAPI derives
field constraints from that model. Existing transaction, PKCE, nonce, signature,
audience, authorized-party, time, role and durable-session admission is unchanged.

The HTTP policy inventory includes this query model. Its provider-owned
`session_state` spelling is one exact model-and-field exception to the
Coordinator JSON request naming convention, not a general snake-case waiver.
The closed Pydantic import vocabulary admits unaliased `ValidationError` for
redacted boundary rejection; policy builders, aliased imports and module-form
bypasses remain prohibited. Catching a broader exception or changing provider
wire names would alter behavior without solving the inventory mismatch.

In Keycloak-enabled mode, both workbench paths and GET/HEAD require a fresh
opaque human session and the existing `read` role before returning HTML:

```text
WorkbenchHtml => FreshHumanSession and ReadRole
MissingOrExpiredSession => fixed local login-start redirect and no HTML
AmbiguousCredential or MissingReadRole => forbidden and no HTML
SessionDependencyUnavailable or Overloaded => unavailable and no HTML
```

Bearer and duplicate-cookie requests cannot become browser authority. A
forbidden or unavailable result cannot enter an automatic login loop. The root
redirect remains stateless; the workbench performs admission. Static assets
contain no user data and remain public; API authorization, disabled mode and
non-Keycloak static delivery remain unchanged. All page outcomes are no-store.

This reuses session and role owners instead of adding proxy authentication or a
second login framework. It intentionally changes anonymous page navigation and
requires issuer metadata, but does not grant new API permissions. Revisit the
closed query profile when another provider extension is admitted. Real login,
logout and late-login revocation remain separate deployment proof obligations.

## Repository-Independent Ping

[GitHub documents](https://docs.github.com/en/webhooks/webhook-events-and-payloads#ping)
`ping` for GitHub Apps as well as repository hooks; repository identity is
conditional. The [Octokit payload schema](https://github.com/octokit/webhooks/blob/main/payload-schemas/api.github.com/ping/event.schema.json)
identifies `zen`, `hook_id`, and `hook` as its core fields. Unconditional
repository extraction therefore rejects a legitimate app-level connection test.

Introduce `PingDelivery`, containing only admitted delivery provenance. Do not
make repository identity nullable on existing repository-scoped outcomes.
The ping grammar requires a string `zen`, a positive safe integer `hook_id`, an
object `hook` with the same positive safe integer `id`, and absent/null `action`.
Unused hook metadata, URLs, sender and optional scope data remain inert: they
are neither fetched nor treated as installation, owner or configuration proof.
This is an admitted core projection, not validation of every provider field.

The version-2 ingestion profile explicitly admits `ping` with no action. After
HMAC admission, exact body binding and strict bounded JSON, event-family
binding must succeed before the ping grammar bypasses repository extraction.
The prepared ping uses the existing durable claim path before acknowledgment.

```text
PingAcknowledged
  => signature admitted and exact body hash and bounded strict JSON
     and header = ping and no recognized CI payload family
     and valid ping core and supported profile and delivery claim committed
PingDelivery => no seed and no workflow observation and no preparation offer
```

Duplicate, conflict, storage-unavailable and cancellation behavior is unchanged.
In particular, relabelling a signed push, pull request, merge group, workflow job
or workflow run as `ping` cannot consume its delivery as a successful ping.
The HTTP response reuses the existing ignored-webhook shape, without exposing
payload data or claiming queued work. The internal versioned profile evolves;
the HTTP response algebra and database schema do not.

The existing proof-owner retirement manifest binds both removed v1 resource
paths to their exact predecessor witness rows and live v2 successors. A rename
is not permission to ignore an unbound proof path. Current roadmap projections
record completed source and bounded deployment evidence separately from open
authentication, provider delivery, capacity and activation obligations.

A router-level early success would skip domain and durable guarantees. Making
all no-op repository fields optional would weaken unrelated contracts. A small
typed outcome and family normalizer preserve those invariants at lower semantic
cost. Revisit if a new control event requires actual provider effects; do not
generalize this no-authority path into an effect dispatcher.

## Evidence Boundary

Tests must vary every ping core operand independently, all five CI families,
optional scope, delivery outcomes and raw-body validity. Root tests cover both
methods, malicious query input, disabled UI and unknown paths. Native worker
serialization and HTTP mapping must include the new result. Packaging must
prove byte identity of source and bundled version-2 profiles.

Browser witnesses include the provider-shaped four-field callback, each missing
or repeated required field, optional metadata bounds, wrong issuer, unknown
fields, callback cleanup, and rejection before exchange. Page witnesses vary
both paths and methods, session outcomes, read role, ambiguous credentials and
dependency failures, proving absence of HTML on every rejection. Valid bundle
fixtures include an actual asset: failing during fixture admission is not a
root-route or authentication witness.

The existing HTTP-admission mutation suite additionally falsifies
header-conditional ping recognition, equality-only ID validation and omission
of the profile veto. Equal numeric values with distinct JSON types, matching
invalid IDs and both mixed-family header directions remain separate oracles;
positive safe-integer boundary controls prevent accidental over-rejection.

Static checks and GitHub tests are distinct from remote deployment checks.
Synthetic signed probes do not prove public GitHub delivery, Keycloak login,
installation authority, selective CI safety or production readiness. The DEV
runtime stays non-enforcing. Public ingress continues to exclude UI and metrics.
