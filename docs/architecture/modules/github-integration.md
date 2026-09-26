# GitHub Integration Module Specification

Status: as-built module specification

Last updated: 2026-09-05

## 1. Owned Invariant

GitHub API adapters convert external API responses and failures into typed
domain ports without owning semantic success or planning decisions.

## 2. Public API

```text
ActionsClient
AppIdentityClient
ChecksClient
DiffClient
GitHubProviderInventory
GitHubWorkflowSnapshotReader
GitHubWorkflowAuthorityReader
GitHubGovernanceObservationReader
GitHubReviewerProviderAdapter
GitHubReviewerPermissionReaderAdapter
RepositoryInventoryClient
RunnerClient
GitHubRunnerSnapshotProvider
WorkflowCatalogClient
```

## 3. Adapter Rules

- adapters return typed success or unavailable states.
- adapters do not throw untyped API errors across domain boundaries.
- adapters do not import planner, verifier, or policy compiler.
- adapters preserve response provenance required for audit.
- adapters enforce API version configuration at the edge.

## 4. Failure Behavior

```text
GitHubRateLimited => typed unavailable with retry metadata
GitHubForbidden => typed authorization failure
GitHubNotFound => typed missing resource
GitHubPartialResponse => typed incomplete evidence
NetworkTimeout => typed unavailable
```

For a pull-request planning request, complete diff evidence requires all of
these provider facts:

```text
request.ref = refs/pull/{request.pull_request_number}/merge
and PullRequestMetadataBefore = (number, base_sha, head_sha)
and PullRequestFilesPaginationIsComplete
and PullRequestMetadataAfter = PullRequestMetadataBefore
and NormalizedPullRequestFiles = Compare(base_sha, head_sha).files
and Compare(base_sha, head_sha) is complete
```

The immutable SHA comparison closes the force-push ABA case that two equal
metadata reads alone cannot exclude. GitHub's compare response has a bounded
file surface; when it is truncated, the adapter returns incomplete evidence and
the planner selects FullCI rather than trusting the larger mutable PR listing.
The Compare response does not expose a total changed-file count and returns at
most 300 changed files. Therefore fewer than 300 files prove that this surface
is complete, while exactly 300 files are ambiguous. A pull-request request may
disambiguate exactly 300 only when its independently paginated, epoch-stable PR
file listing is complete and equal to the Compare files; a push request with
exactly 300 files remains FullCI-invalidating. This rule follows the documented
provider limit rather than inventing an unavailable `total_files` field.

For HTTP `403`, rate-limit evidence takes precedence over `GitHubForbidden`:
GitHub documents both primary exhaustion (`remaining = 0`) and secondary-limit
backoff (`retry-after`) on `403` or `429`. Without those headers, a bounded
strict JSON error object can identify a secondary limit: its message must equal
`You have exceeded a secondary rate limit` (optionally followed by a period)
or begin with that complete sentence and a space. The object is limited to
4,096 bytes, kernel depth limit two (root depth zero) and 16 value nodes;
only `message`, optional textual
`documentation_url` and optional string status `403` are admitted. Message and
documentation text each have a 1,024-byte bound and exclude ASCII control characters.
Other shapes remain forbidden; a URL alone is not evidence. Provider text is
never copied into a public failure. This is a bounded recognizer, not an
exhaustive provider-message grammar.

A genuinely absent selected-version header on HTTP `500`, `502`, `503` or `504`
retains the existing non-success/timeout classification and raw response.
A present empty/duplicate header is not absence. Explicit version mismatch and
all `2xx` responses retain provenance enforcement; other statuses retain their
existing precedence. This exception cannot produce successful evidence.

Provider inventory and governance observation share one retry-seconds projection.
It preserves explicit `Retry-After` and may additionally derive a primary-reset
delay only for `remaining = 0` with admitted epoch seconds and a trusted aware
receipt instant. The transport samples its existing injected `Clock` after the
bounded exchange for `403`/`429`; legacy responses without that optional instant
cannot supply reset-derived timing. The provider `Date` header is not time
authority. Conversion rounds up and combines applicable hints without shortening
either. Missing, malformed, duplicate or unrepresentable timing remains unknown;
delays over the existing 3,600-second API bound are not clamped. A positive
remaining count does not make its reset an applicable secondary-limit delay.
The value is a bounded wait hint, not proof that every provider constraint is
known or that availability returns at that instant. No default wait, automatic
retry, cooldown, quota reservation or retry-budget change follows from it.

## 5. Proof Obligations

| Obligation                                                             | Falsifier                                                                                                                                                                          |
|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| API failure is not implicit success.                                   | Diff timeout becomes empty diff.                                                                                                                                                   |
| PR diff identity is immutable.                                         | PR metadata reads `A, A` while paginated files came from an intermediate `C`, yet reduced CI is admitted.                                                                          |
| Provenance is retained.                                                | Diff context lacks source pagination metadata.                                                                                                                                     |
| Required check source identity is preserved.                           | Check run app id discarded.                                                                                                                                                        |
| Runner snapshot is not reservation.                                    | Adapter marks runner as reserved.                                                                                                                                                  |
| App and installation credential planes remain distinct.                | An installation token reaches `/app/installations/{id}` or an app JWT reaches `/installation/repositories`.                                                                        |
| Installation-token capability is closed.                               | An unknown operation, method, path shape, dot segment, query order/value, or request body reaches credential acquisition or provider I/O, including after HTTP path normalization. |
| Reviewer, app-JWT, and installation credential planes remain distinct. | A reviewer token reaches an app-only route, or an app/installation credential reaches reviewer identity resolution.                                                                |
| Reviewer-token capability is exact and ephemeral.                      | A mutation, unknown operation, body, wrong repository id, or retained reviewer token reaches provider I/O or durable state.                                                        |
| Exact repository relation is conjunctive.                              | Reviewer access without the matching App installation and activation-time permission recheck becomes activation authority.                                                         |
| Catalog pagination is not invented.                                    | Missing, foreign, duplicate, or contradictory Link evidence becomes a successful page.                                                                                             |
| Workflow source identity is content-addressed.                         | A commit, tree, blob, size, or recomputed Git blob mismatch becomes admitted source evidence.                                                                                      |
| Remote workflow syntax does not trigger ambient I/O.                   | A discovered remote `uses` coordinate is fetched without a separately authorized resolver.                                                                                         |

## 6. Implementation Mapping

Primary owned files:

```text
ci_coordinator/integrations/github/app_client.py
ci_coordinator/integrations/github/actions_client.py
ci_coordinator/integrations/github/checks_client.py
ci_coordinator/integrations/github/diff_client.py
ci_coordinator/integrations/github/runner_client.py
ci_coordinator/integrations/github/runner_capacity.py
ci_coordinator/integrations/github/runner_capacity_decoding.py
ci_coordinator/integrations/github/workflow_catalog_client.py
ci_coordinator/integrations/github/app_transport_profile.py
ci_coordinator/integrations/github/app_http.py
ci_coordinator/integrations/github/app_credentials.py
ci_coordinator/integrations/github/app_transport.py
ci_coordinator/integrations/github/installation_request_admission.py
ci_coordinator/integrations/github/request_admission.py
ci_coordinator/integrations/github/_response_decoding.py
ci_coordinator/integrations/github/reconciliation_observer_decoding.py
ci_coordinator/integrations/github/provider_inventory.py
ci_coordinator/integrations/github/provider_inventory_decoding.py
ci_coordinator/integrations/github/_reviewer_response.py
ci_coordinator/integrations/github/reviewer_attestation.py
ci_coordinator/integrations/github/reviewer_permission.py
ci_coordinator/integrations/github/workflow_discovery.py
ci_coordinator/integrations/github/workflow_discovery_client.py
ci_coordinator/integrations/github/workflow_discovery_decoding.py
ci_coordinator/integrations/github/repository_context_decoding.py
```

`_response_decoding.py` owns only strict JSON-object and safe-integer
primitives shared by provider response decoders. Each decoder retains its own
endpoint schema, fallback mapping, and domain projection.

OIDC JWKS retrieval is excluded from this module and belongs to the JWKS
provider. GitHub API integration cannot make a JWT verification or key-rotation
claim.

The runner snapshot provider combines the low-level repository-runner inventory
with every repository-visible organization runner group and its membership. It
projects capacity only after `runner_capacity` supplies exact static selectors;
the integration owns bounded provider acquisition, not eligibility or shard
policy. Any ambiguous organization denial, incomplete pagination, malformed
body, unstable total, or bound violation returns unknown capacity.

## 7. Acceptance Tests

- fake GitHub adapters for domain tests.
- typed failure mapping tests.
- pagination, truncation, runner eligibility, and runner-group denial tests.
- check source app identity tests.

## 8. GitHub App Runtime Transport

The protocol clients are pure consumers of `GitHubTransport`. Runtime
composition adds one concrete GitHub.com-only transport factory without changing their
success algebra. It owns application JWT construction, installation-token
acquisition and cache safety, bounded HTTP exchange, and resource closure.

```text
GitHubAppTransportFactory.for_installation(installation_id)
  -> GitHubTransport
GitHubAppTransportFactory.for_app()
  -> exact installation-identity GitHubTransport
GitHubTransport.send(request)
  -> GitHubResponse | GitHubTransportFailure
GitHubAppTransportFactory.aclose()
```

The factory is the sole owner of its HTTP client, credential cache, and
per-installation single-flight locks. A returned transport is a lightweight
installation binding; it does not own a second client or a second cache.
Runtime composition may supply one admitted canonical credential-free outbound
proxy URL. The factory passes it explicitly to its sole HTTP client while
retaining `trust_env=False`; no ambient proxy or bypass variable can alter the
route. An injected test transport and the live proxy are mutually exclusive.

The app-authenticated binding admits exactly two bodyless, query-free reads
with their exact protocol operation identities: `GET
/app/installations/{installation_id}` and `GET
/repos/{owner}/{repository}/installation`. Every other request fails before JWT
construction or provider I/O.
The installation binding mints and caches only the token for its own positive
safe installation identity. Before credential acquisition, it admits only the
closed set of read operations consumed by the repository, Actions, checks,
diff, runner, workflow-catalog, workflow-discovery, and provider-inventory
clients. Each operation is bound to `GET`, an exact canonical path shape, an
exact query name/order/value contract, and an empty body. An unknown operation
or a mismatched request fails without reading credentials or performing
provider I/O. Canonical path admission is performed before URL construction;
raw or decoded dot segments, non-canonical encoding, oversized paths, and path
shapes that an HTTP client could normalize into another endpoint are rejected.

The reviewer-token binding is a separate, callback-scoped capability. A
proposal-bound GitHub OAuth exchange requires PKCE S256, an exact redirect URI,
one bounded non-redirecting response, canonical JSON media type and framing, and
an explicitly expiring bearer. The token may perform only bodyless reads of the
current user and the exact immutable repository id bound by the pending
transaction. It is discarded when reviewer identity and `maintain` or `admin`
evidence have been projected; it never enters the Keycloak session or durable
state. Timeout, HTTP `408`, `429`, `5xx`, malformed identity, and malformed
permission evidence become typed unavailability rather than approval.

Activation performs a separate App installation-token recheck of the exact
retained reviewer id and login. The reviewer OAuth exchange receives the same
immutable proxy value as the shared App API factory. This is transport wiring
only and does not merge reviewer-token, installation-token, or app-JWT
authority.

```text
AuthenticatedSend(I, R) iff
  PositiveInstallation(I)
  and RequestVersion(R) = AdmittedApiVersion
  and Credential(I) is unexpired beyond refresh skew
  and I is a positive safe installation identity
  and RequestPath(R) remains within the fixed origin
  and RequestHeaders(R) cannot override authorization or transport framing
  and HTTPSRequestIsBoundedAndNonRedirecting(R)

not AuthenticatedSend(I, R)
=> GitHubTransportFailure
=> GitHubProtocolClient cannot produce GitHubSuccess
```

The application JWT has `alg = RS256`, `iat = now - 60 seconds`, bounded
`exp`, and the configured app identifier as `iss`. The implementation requests
an installation token only from the fixed GitHub.com REST endpoint and sends
that token only in the generated bearer header. It never returns, logs, or
embeds either credential in a public result or exception.

The versioned machine profile
`docs/specs/ci-coordinator-runtime/github-app-transport-profile.v1.json` owns
the endpoint, API version, header values, JWT bounds, cache skew, and byte or
time limits. It also bounds installation identities, cache cardinality,
simultaneous refreshes, and all HTTP exchanges admitted by one shared transport
factory. The concurrency permit covers credential refresh and ordinary API
calls, while its wait remains inside the same absolute exchange deadline. Each
logical send has one absolute deadline across credential-cache lookup,
refresh-slot admission, token acquisition, HTTP exchange, and all response
chunks; per-operation HTTP timeouts do not substitute for that bound. The
values are runtime composition behavior, not repository policy and not a
dynamic user configuration.

The HTTP client requests `Accept-Encoding: identity`. Before response-body
iteration, the transport rejects any `Content-Encoding`, repeated or
non-canonical `Content-Length`, and declared length above the profile maximum.
It consumes live `httpx2` response streams as raw bytes, not decoded bytes, and
enforces the aggregate profile maximum while buffering. A caller-injected
transport may instead return an already materialized response; that trusted
composition seam receives the same aggregate byte check but cannot provide a
pre-allocation or wire-byte claim. This removes transparent decompression from
the application-owned network read path without overstating what can be proved
about an injected transport. The bound also does not claim that the underlying
network stack cannot allocate one transport chunk before yielding it; that
chunk size remains an HTTP-client and network-transport fact.

### 8.1 Failure And Cancellation Algebra

```text
credential response malformed | expired too soon | credential endpoint failure
  => GitHubTransportFailure(unavailable)

request timeout => GitHubTransportFailure(timeout)
network error | redirect | response encoding | body limit violation
  => GitHubTransportFailure(unavailable)
task cancellation => propagated cancellation
close during an accepted send => drain before client close
```

Cancellation is deliberately not converted to `unavailable`: callers that own
shutdown or request cancellation must retain that control signal.

### 8.2 Non-Claims

This transport does not grant GitHub App permissions, approve any repository,
choose a GitHub Enterprise endpoint, retry non-idempotent mutations, interpret
API payloads, or prove live provider availability. A valid local JWT or a
mocked token response is not credential custody, deployment, or
authority-bearing proof.
The transport-factory-local concurrency cap does not prove provider-wide
concurrency, primary-rate, secondary-rate, or CPU budgets because additional
factories, replicas, and integrations may share the same provider accounting
scope and some point costs are undisclosed.
