# API HTTP Module Specification

Status: module specification

Last updated: 2026-09-02

## 1. Owned Invariant

HTTP routes translate authenticated transport requests into application use-case
calls and translate typed results back into stable HTTP responses without owning
domain policy.

## 2. Public API

The [HTTP reference](../../reference/http-surface.md) summarizes operations
and links to their guides. The conditional router composition in
[`api/http/app.py`](../../../backend/src/ci_coordinator/api/http/app.py)
determines which route families exist for an admitted dependency set; the
summary is not a second complete route catalog.

The provider inventory routes project
an authorized installation catalog and one bounded repository page; they do not
create repository command authority. The repository workbench route projects
bounded plans, reconciliation runs, config history, overrides, and scoped audit
events. The workflow-discovery route returns one exact-commit static evidence
report and a reviewable observe-only proposal or typed blockers; it adds no
mutation authority. Governance observation projects bounded best-effort current
provider evidence. Governance baseline read and approval separately expose an
explicit append-only expected state; they do not classify compliance or drift.
The optional Keycloak routes create and resolve only opaque server sessions;
back-channel logout consumes one verified logout token. Control-plane
mutations require one exact credential plane, the operation's role, and browser
integrity where applicable. Repository review separately requires a one-use
GitHub reviewer step-up bound to the exact session and proposal. Config
activation then requires the `activate` role, the retained receipt, unchanged
proposal identity, and a fresh GitHub App permission recheck. Current economics operations are described by
[Measure And Compare A CI Command](../../how-to/measure-and-compare-ci.md);
an available measurement API does not establish consumer savings. The plan route accepts
`dynamic-ci-plan-request/v2` and returns
`dynamic-ci-signed-plan-envelope/v1`. Route-path equality does not create an
external compatibility contract; only the versioned schemas do.

The configuration lifecycle exposes a finite API-first projection before its
UI consumer: effect-free validation, operation-idempotent registration,
activation, rollback, bounded status traversal, and exact retained-source
export. Validation and registration share one policy-admission capability.
Status never returns source bytes; export scope-filters and re-admits the
retained draft before emitting it. The exact operation map and bounds are owned
by `config-lifecycle-http-profile.v1.json` and checked against OpenAPI before
the TypeScript projection is generated.

The runtime origin does not serve `/openapi.json`, Swagger UI, ReDoc, or the
Swagger OAuth redirect. Schema generation remains an in-process build and test
surface through `app.openapi()`; it is not an origin route and cannot load
third-party executable assets in a session-bearing browser context.
The operator UI projection invokes the same `create_app` composition root with
an exact UI-only dependency set. A second manually assembled router graph is
forbidden because it could diverge from middleware and generic error policy.
Its startup-admitted in-memory asset snapshot serves only manifest members.
Build and runtime admit the exact path/size inventory, every per-file limit,
and the aggregate bundle limit before materializing any asset payload. An
unversioned member returns a `no-store` redirect into the exact canonical
manifest SHA-256 namespace; only that content-addressed namespace receives
one-year immutable caching. GET and HEAD expose the same length, media type,
validator, cache, and security metadata, but HEAD transfers no body. Weak and
wildcard validators across all repeated `If-None-Match` field lines use HTTP
conditional-request semantics.

The no-store shell projects parsed `script`, `link` and `img` resource
`src`/`href` attributes directly into that namespace once at startup, after raw
snapshot verification. References
must identify manifest members exactly; URL aliases, absent members, duplicate
attributes, inline scripts, and `base` elements fail admission. Both the
projected index and final snapshot retain the existing byte bounds. The raw
index remains hashed by the unchanged manifest; the derived shell does not
feed back into the bundle digest. JavaScript, CSS and on-disk files are unchanged.

The production Vite build uses its native [relative base](https://vite.dev/guide/build.html#relative-base),
`./`, for intra-bundle imports, preloads and assets. The
[HTML module map](https://html.spec.whatwg.org/multipage/webappapis.html#fetch-a-single-module-script)
keys modules by request URL but resolves imports against response URL. Therefore
the built graph must request the canonical namespace directly, without using
compatibility redirects. Neither a `base` element nor inline script/CSP changes
are allowed. Authentication, no-follow snapshot admission, raw hashes and exact
inventory, GET/HEAD metadata, immutable asset ETags and old-bundle 404 remain
unchanged. Deployment recovery and automatic reload are separate contracts.

The native browser oracle uses the actual `mount_operator_ui` transport, not
Vite preview or a lookalike asset server. It checks canonical module identity,
single entry evaluation, actual CSP and state retention across lazy navigation.
Mocked API responses establish neither authentication nor provider evidence.

Any production consumer must bind to these schemas explicitly. A future schema
replacement requires a versioned contract or an admitted atomic consumer
migration; local OpenAPI evidence cannot prove that external migration occurred.

Formally:

```text
CanonicalProtocol :=
  dynamic-ci-plan-request/v2
  + dynamic-ci-signed-plan-envelope/v1

ProtocolReplacement
=> NewVersion or AtomicConsumerMigration

not ExternalDeploymentEvidence
=> CanonicalProtocol is not a production-availability claim
```

## 3. Private Boundary

The `api.http` package initializer is an inert namespace. Internal callers
import `create_app` from `api.http.app`, dependency records from
`api.http.dependencies` and the plan authenticator from
`api.http.plan_authentication`. Importing the namespace or body-limit middleware
must not load the application or router graph. Explicit application import
retains the existing single composition root. The rationale and bounded
source-contract change are in [HTTP Import Startup](../../features/http-import-startup.md).

The module owns FastAPI routers, transport-shaped authenticators, dependency
wiring, request/response DTOs, OpenAPI generation, generic middleware assembly,
and transport error mapping. It also owns the narrow webhook ingress port whose
input is only canonical duplicate-capable header lines and exact body bytes;
this port exists because its runtime adapter crosses the process-isolation
lifecycle and grants no trusted identity or domain authority. Behavioral input
ports belong to their capability or to cross-context application orchestration.
HTTP must not own those ports, planning semantics, identity verification
internals, persistence transactions, concrete service implementations, or
GitHub API adaptation.

### Representation Models

Every public Pydantic DTO derives from the request or response policy in
`api/http/model_contracts.py`. Where wire and Python names differ, request
models admit only an explicit canonical validation alias and reject the
Python-name alternative. Only the plan request retains serialization aliases
because its exact parser path consumes a serialized projection. Response
models accept only internal Python names and emit
lower-camel serialization aliases through one JSON-mode mapping operation. The
bounded `ProjectedResponseModel` role additionally admits trusted immutable
record attributes for an exhaustive DTO inventory. The historical readiness
key `unavailable_dependencies` is no longer part of the public readiness DTO;
that response contains only `ok` and `status`. Dependency identifiers remain
behind the private diagnostics and protected metrics boundary.
The two profile-capacity discriminator fields retain explicit
`executionKind` aliases solely because FastAPI uses field alias metadata for
OpenAPI discriminator names; alias validation remains disabled.

Both policies reject extras, undeclared scalar coercion, invalid defaults, and
non-finite numbers. Request and response models are shallow frozen; response
models revalidate nested model instances but make no deep-immutability claim. The
only admitted request conversion is canonical extended ISO-8601 datetime text
to `datetime` for the force-full-CI expiry; domain policy still decides
timezone and temporal validity.

The exhaustive policy witness rejects direct `BaseModel`, local `ConfigDict`,
unowned or aliased Pydantic imports, Pydantic module imports,
escape or shadowing of admitted Pydantic policy callables,
`populate_by_name`, Pydantic field aliases outside the exact discriminator
inventory, redundant strict scalar wrappers, and direct model dumping outside
the owner module. Generated OpenAPI and TypeScript remain exact consumer
projections. The design and alternatives are owned by
[HTTP Model Contracts](../../features/http-model-contracts.md).

## 4. Input Completeness Rules

Every route must validate:

```text
schema version
request id or idempotency key when required
repository and run identity references when required
payload size limits
authentication context dependency
```

Incomplete or invalid transport input must fail before application use cases are
called.

Control-plane routes reject ambiguous credential planes. Safe reads require one
admitted Keycloak human session or workload bearer with the exact role. Browser
logout, repository-attestation start, config mutation, override mutation, and
governance baseline approval additionally require the exact configured
`Origin` and session-derived CSRF header. Each mutation route has a dedicated
raw body ceiling before DTO parsing or authentication.

`POST /api/v1/dynamic-ci/plan` additionally admits at most `65,536` raw body
bytes. The bound applies to both declared `Content-Length` and incrementally
received body chunks, before DTO parsing or authentication. A malformed
duplicate or non-decimal `Content-Length` is `400`; an oversized body is the
redacted typed error `413 {"code":"request_too_large"}`. This preserves the
prior route's byte ceiling while not trusting a client-provided length. Decimal
length admission is itself bounded: an arbitrarily long digit sequence is
classified without first materializing an unbounded integer.

The route parses the bounded body before authentication and passes the exact
`PlanRequest` to `PlanRequestAuthenticator.authenticate(authorization, plan_request)`.
Exactly one raw `Authorization` field is required. Missing or repeated fields
are rejected before the authenticator, every `401` carries
`WWW-Authenticate: Bearer`, and OpenAPI names the GitHub Actions OIDC security
scheme. This prevents a proxy and the application from selecting different
credentials from an ambiguous field set.
The authenticator derives expected OIDC repository, ref, run, attempt, event,
and workflow claims from that parsed value; it does not reparse transport JSON.
The `PlanRequest` domain contract requires
`ref = refs/pull/{pull_request_number}/merge` before key resolution. Thus a
token whose trusted ref names PR 42 cannot authenticate a body naming PR 43.

```text
ParsedPlanRequest = P
and AuthenticatorAccepts(authorization, P) = TrustedRun
=> UseCaseReceives(TrustedRun, P)

AuthenticatorAccepts(authorization, P) without P
=> a token for one run can be presented with another request
```

This order is necessary because a cryptographically valid token is insufficient
unless its claims bind the same immutable run identity consumed by issuance.

`POST /webhooks/github` applies the profile-owned raw-body maximum before
signature verification, preserves the received bytes and duplicate header
field-lines, rejects any `Content-Encoding`, and admits only the exact JSON
media contract. Signature verification, body hashing, strict JSON parsing,
event-family binding, and normalization execute in one cancellable no-queue
worker process per backend process. The runtime worker composes
`identity_admission` with the capability-owned `github_ingestion`
preparation function; the HTTP boundary owns only the immutable ingress command
and result port, and no application pass-through owns the same call. Its
transport failures are a separate redacted contract:
invalid trust input is `401`, body-limit failure is `413`, delivery conflict is
`409`, busy or unavailable admission is `503`, and an unexpected route failure
is `500`. A webhook failure never adopts the dynamic plan route's response
schema.

The HMAC authenticates body bytes, not the delivery or event headers. Durable
idempotency therefore admits the body hash as replay identity and uses delivery
id only as provider correlation. The parsed payload family must equal the exact
event header. Repeating the same signed body under another delivery id emits no
new effect; reusing one delivery id with different body bytes is a conflict.

The current webhook use case completes synchronously. A `200` response proves
signature admission, bounded normalization, and a durable delivery claim only;
the response reports `downstream: best_effort_preparation` only when an
admitted seed was offered to the process-local preparation worker, otherwise
`downstream: none`. Neither outcome proves durable queuing, completed
preparation, a reconciliation transition, or a planning decision. `202` is forbidden until a durable inbox owns later processing.

Every non-liveness HTTP route is inside one runtime-owned absolute deadline.
Body-bearing routes additionally require an exact method-and-path byte maximum,
a path-isolated no-queue body lane, and one process-wide weighted retained-body
lease. A request that cannot acquire either lease receives an immediate typed
overload rejection and never becomes a semaphore waiter. The weighted lease
reserves the canonical `Content-Length` when present and the route maximum when
absent, and remains held through downstream completion while replayed bytes can
still be retained. Therefore the sum of admitted body reservations never
exceeds `maximum_retained_body_bytes`, independently of the sum of path-local
maxima. Lanes remain path-isolated, so slow planning cannot consume rollback or
configuration permits.

The common single-chunk path reuses the received immutable `bytes`; a
multi-chunk body requires one join allocation. The retained-body budget does
not claim an allocator or RSS bound, so capacity qualification measures the
transient copy envelope separately. The absolute deadline continues across
admission, buffering, authentication, provider/database work, and response
completion. Deadline expiry and admission overload have distinct stable typed
responses. A downstream `TimeoutError` without owner-deadline expiry remains
an unexpected `500` defect.

Request admission reserves the smaller of one second and 10% of its configured
budget for a failure response. This reserve is inside the absolute budget;
ordinary work therefore stops earlier rather than extending the request after
timeout. Overload and timeout sends have a finite deadline, and a timeout
releases its work-class permit before attempting delivery. If delivery itself
exhausts the budget, cancellation propagates without another application-level
generic error response. This ASGI admission bound does not prove socket delivery,
Uvicorn fallback I/O, scheduler latency or the completion of independently owned
cleanup; those require their declared cooperative/cleanup and deployment bounds.

Configuration registration admits source validity by UTF-8 bytes, not Python
character count. Its transport envelope allows the worst-case six-byte JSON
escape expansion plus fixed framing, while the DTO and domain both enforce the
same 2,097,152-byte source ceiling. CPU-bound policy admission executes in one
no-queue cancellable worker process per backend process, so the absolute
request deadline can terminate the worker instead of waiting for synchronous
event-loop parsing. A concurrent registration receives typed unavailability
without submitting more parser work. Activation and rollback retain the
smaller operator-command body bound.

The observability routes are read-only projections. `/healthz` asserts process
liveness only. `/readyz` returns only `ready` or `not_ready`; exact dependency
classification remains inside bounded metrics and private diagnostics. A
connected runtime requires one distinct, header-safe constant-time metrics
bearer that cannot reuse another configured credential secret before rendering
`/metrics`; token comparison uses fixed-length digests and every
metrics projection is `no-store`, while deployment network isolation remains
independently required. Disabled local runtime may expose process metrics without that
connected credential. These routes neither decide planning nor claim
compatibility with an unadmitted external observability payload.

The workbench route authenticates the operator before use-case execution,
admits positive safe-integer path identities and a per-section limit in `[1,
20]`, and authorizes the exact repository scope before any persistence or replay
access. `401`, `403`, `422`, and `503` are typed and redacted. A successful
response is not a whole-ledger success claim: its replay status is explicitly
bound to the snapshot ledger revision.

The installation catalog route authenticates before resolving a bounded
installation grant and before any GitHub request. A complete empty catalog is
valid only for an empty grant; an independently failed installation remains a
typed partial row. The repository catalog route admits a positive safe
installation identity, page in `[1, 10000]`, and `perPage` in `[1, 100]`, then
revalidates installation identity and eligibility before using that exact
installation's credential. Its `401`, `403`, `404`, `409`, `429`, and `503`
responses are typed and redacted; provider bodies never cross the route.

The workflow-discovery route authenticates and delegates exact scope
authorization before provider reads, admits only an optional lowercase 40-hex
revision, and returns the exact resolved revision. Direct discovery and
proposal review share one capability-owned no-queue snapshot-reader admission
before credential or provider I/O; the direct route's local limiter only sheds
earlier and is not the resource invariant. Saturation is a distinct typed
unavailable outcome. Discovery therefore cannot consume the complete shared
GitHub exchange budget through an alternate entry point. Its DTO omits source
bytes, scripts, credentials, durable review state, and internal policy compiler
objects.

The governance-observation route resolves an exact current repository read
grant before provider access and projects only a bounded, re-bound,
best-effort, unbaselined state. Baseline read requires a fresh exact scope
grant before persistence. Baseline approval additionally requires exact
Origin and CSRF admission, binds one requested observation digest and complete
active predecessor pointer, and accepts no provider rule bytes from the
browser. Exact operation replay precedes a fresh provider observation. Every
successful retained response round-trips the complete canonical state and is
explicitly expected state rather than compliance evidence.

Browser login start redirects only to the profile-admitted Keycloak
authorization endpoint. Callback query multiplicity, transaction-cookie state,
OIDC code flow with PKCE, token verification, and identity are delegated to the
control-plane identity capability; the route clears the transaction cookie and
emits `Cache-Control: no-store` on every admitted result. Session and logout
responses are bounded and no-store. Repository-attestation start binds the
submitted operation and proposal before redirecting to GitHub; callback
consumes the exact transaction once, so a concurrent loser cannot relabel or
duplicate the retained review.

Login start and callback each own an independent finite process-local token
bucket and return `429` with `Retry-After` without queueing when exhausted.
This bounds one process contribution; the ingress owner still owns source-wide
and replica-wide abuse controls. Uvicorn access logging is disabled so OAuth
`code` and `state` query values do not enter generic access logs. Forwarded
header trust is disabled; the edge owner validates public hosts and forwarding
metadata, while application authorization never derives authority from `Host`.

## 5. Fallback Behavior

```text
InvalidTransportInput => 4xx typed error
Unauthenticated => 401 typed error
Unauthorized => 403 typed error
UseCaseUnavailable => 503 typed error or FullCI fallback envelope when the route owns bootstrap response translation
UnexpectedException => 500 {"code":"internal_error"} with no-store plus one bounded private diagnostic
```

For the dynamic-plan authentication boundary, the transport algebra is exact:

```text
InvalidCredential => 401 {"code":"unauthenticated"}
ForbiddenIdentity => 403 {"code":"forbidden"}
AuthenticationDependencyUnavailable => 503 {"code":"plan_unavailable"}
UnknownOidcRejectionReason => 500 {"code":"internal_error"}
```

These bodies disclose only the HTTP class. OIDC reason codes, token content,
claim values, JWKS failure kinds, provider messages, and unexpected internal
classification details remain confined to internal typed handling and bounded
metrics.

`503` requires a declared unavailable result or exception owned by the relevant
capability contract. An arbitrary exception does not prove temporary dependency
failure or retry safety and therefore remains the generic redacted `500`. OIDC
rejection reasons are admitted by exact finite sets; any other reason is an
internal classifier defect and cannot enter the availability algebra.

Routes cannot convert domain rejection into selected validation.

## 6. Audit And Replay Facts

The API layer may record request id, route id, response class, authenticated
principal reference, and redacted error code. It must not persist raw secrets,
tokens, signatures, or request bodies that contain credentials.

Every HTTP request receives a service-generated 128-bit correlation identity.
The service ignores caller-supplied correlation identities for internal
authority, binds its identity to the async request context, exposes it as
`X-Correlation-ID`, and clears the context after completion. Correlation is
diagnostic metadata and never participates in authentication or planning.

## 7. Forbidden Imports

```text
sqlalchemy
integrations and provider clients
known concrete service implementations
planning_core internals
verification_core internals
GitHub SDK clients
domain private modules
```

## 8. Proof Obligations

| Obligation                                                     | Falsifier                                                                                                                        |
|----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| Routes call public capability or application input ports only. | Route imports a provider adapter or concrete service implementation.                                                             |
| OpenAPI matches public DTOs.                                   | Snapshot changes without contract review.                                                                                        |
| Every retained route has one implementation owner.             | A supported route is absent from the stage-to-route inventory.                                                                   |
| Invalid transport input is rejected.                           | Missing repository identity reaches planner.                                                                                     |
| OIDC identity binds the parsed request.                        | A token for run A authenticates parsed request B.                                                                                |
| Bearer fields are unambiguous and documented.                  | Duplicate Authorization fields reach authentication or OpenAPI declares no security.                                             |
| Workbench scope and bounds precede persistence.                | An ungranted scope or limit above 20 reaches the repository.                                                                     |
| Provider inventory authority precedes GitHub I/O.              | An unauthenticated actor, foreign installation, or empty grant reaches the provider.                                             |
| Catalog partiality is truthful.                                | A failed installation or contradictory page becomes complete empty success.                                                      |
| Workflow discovery remains exact and read-only.                | Cross-scope or cross-revision evidence returns success, or the route registers or activates policy.                              |
| Governance observation remains read-only provider evidence.    | A provider observation persists expected state or claims compliance.                                                             |
| Governance baseline approval is explicit and request-bound.    | Wrong scope, Origin, CSRF, digest, predecessor, operation identity, or retained-state digest reaches accepted output.            |
| Governance baseline remains expected state only.               | A baseline response claims compliance, drift, provider enforcement, release readiness, or omission authority.                    |
| Control-plane credential planes remain disjoint.               | Session, workload bearer, or break-glass bearer are ambiguous or one authenticates as another plane.                             |
| Browser mutations are request-bound.                           | Wrong Origin/CSRF, insufficient role, mismatched scope, or an oversized body reaches mutation work.                              |
| Repository attestation remains proposal-bound.                 | GitHub reviewer evidence authorizes a different proposal, direct activation, provider write, dispatch, or omission.              |
| Config activation rechecks authority.                          | Activation succeeds without an exact retained receipt, unchanged proposal, current reviewer permission, and optimistic revision. |
| Error responses are typed and redacted.                        | Raw JWT or signature appears in response.                                                                                        |

## 9. Implementation Mapping

Target files:

```text
ci_coordinator/api/http/app.py
ci_coordinator/api/http/body_limits.py
ci_coordinator/api/http/control_plane_authentication.py
ci_coordinator/api/http/control_plane_security.py
ci_coordinator/api/http/dependencies.py
ci_coordinator/api/http/operator_ui.py
ci_coordinator/api/http/operator_ui_bundle.py
ci_coordinator/api/http/plan_authentication.py
ci_coordinator/api/http/routers/
ci_coordinator/api/http/contracts.py
ci_coordinator/api/http/errors.py
```

## 10. Acceptance Tests

- OpenAPI snapshot tests.
- route-to-use-case import-boundary tests.
- invalid request DTO tests.
- declared and chunked request-body bound tests.
- path-lane and process-wide weighted retained-body saturation tests.
- non-liveness deadline, overload, cancellation, and timeout-ownership tests.
- redacted error response tests.
- bootstrap fallback response contract tests.
- browser cookie, callback, dual-credential, Origin/CSRF, role, replay,
  governance-state identity, predecessor race, and response-binding tests.

## 11. Non-Claims

This module does not prove a deployed ASGI process, live bootstrap behavior, or
dynamic-omission authority. Its in-process limit also does not prove that an
ASGI server, reverse proxy, or load balancer rejects an oversized connection
before supplying a body chunk. The replay-payload bound excludes allocator
overhead, upstream buffering, transient conversion copies, and deliberate
post-request payload copies made by downstream code.
