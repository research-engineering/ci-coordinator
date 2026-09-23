# Production Quality Closure

Status: implemented; provider and production qualification remain external

Frozen baseline: `62f621a5bbc971bbe0ac1afa42cd89834cc52525`

Owners: `ci-coordinator.runtime`, `ci-coordinator.operator-ui`, and the external
production-admission owner

Implementation plan:
[Production Quality Closure Implementation Plan](production-quality-closure-implementation-plan.md)

## 1. Decision

Close every validated contradiction between the current implementation and an
active requirement, make every production-capacity premise executable or an
explicit external obligation, and reject proposed changes that would silently
widen product authority or claim unavailable provider guarantees.

This design uses four terminal dispositions:

- `repair`: current code contradicts an active owner contract;
- `adopt`: the product explicitly accepts a new observable behaviour;
- `qualify`: correctness depends on environment evidence that source code
  cannot manufacture; and
- `retain`: the current bounded non-claim is correct or the candidate finding
  is refuted.

No finding may remain merely "not addressed". A `qualify` or `retain` row is a
decision with an owner, a reason, and a falsifier, not an implicit deferral.

## 2. Formal Admission Rule

For finding `f` and candidate change `c`:

```text
Admitted(c, f) iff
  CurrentEvidenceValid(f)
  and ExactOwner(c)
  and ProtectedObservablesPreserved(c)
  and (
    ClosesActiveContractViolation(c, f)
    or ExplicitBusinessDelta(c, f)
    or MakesExternalObligationExecutable(c, f)
  )
  and FailurePathTotal(c)
  and StrictlyPreferableToAlternatives(c)
```

`StrictlyPreferableToAlternatives` requires no weaker correctness, security,
reliability, or evidence boundary and lower or equal accidental complexity.
Size, novelty, and the label "SOTA" are not evidence.

The following implications are forbidden:

```text
LocalLimiter => ReplicaWideFairness
ForwardedHeader => TrustedSourceIdentity
FilenameSuffix => ContentIdentity
LocalHashChain => ExternalAntiTruncation
ProviderReadSequence => PointInTimeProviderSnapshot
LocalTests => CapacityQualified
```

## 3. Protected Observables

Unless an explicit delta in Section 4 says otherwise, implementation must
preserve all of the following:

1. Unknown, incomplete, stale, malformed, or unavailable evidence selects
   FullCI or typed unavailability, never selected execution.
2. No new provider write, workflow dispatch, config activation, or omission
   authority is introduced.
3. Browser identity remains session based; GitHub provider credentials never
   enter browser-visible state.
4. Public error bodies remain redacted and non-cacheable.
5. Provider observations remain best-effort where the provider exposes no
   atomic snapshot contract.
6. `non_enforcing` remains runnable without production authority.
7. `enforcing` remains fail-closed and may rely only on exact signed external
   evidence already admitted by the production owner.
8. Existing exact request, repository, workflow, epoch, and receipt identity
   bindings remain unchanged.

## 4. Explicit Observable Behaviour Deltas

These are the complete intentional product or operations deltas. Any other
observable change is a regression.

| ID  | Before                                                                                                                                                                                                                 | After                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Reason                                                                                                                                                                                                                                                                                                     |
|-----|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| B1  | A full body lane and an expired request deadline return the same typed body.                                                                                                                                           | Both remain fail-closed, but overload and deadline expiry have distinct stable error codes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Operators and clients can distinguish retry pressure from elapsed work without exposing internals.                                                                                                                                                                                                         |
| B2  | Unexpected exceptions produce a redacted public response but can lose the private cause.                                                                                                                               | The public response is byte-equivalent; a bounded private diagnostic records correlation identity, stage, and exception class only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Public secrecy and private diagnosability are independent obligations.                                                                                                                                                                                                                                     |
| B3  | Public readiness names unavailable dependencies and permits cache reuse across dependency transitions.                                                                                                                 | Public readiness reports only ready/not-ready, every readiness response is `no-store`, and bounded internal diagnostics and metrics retain the classified dependency state.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Dependency topology is not required by a load balancer, while a readiness decision is valid only for the dependency epoch in which it was observed.                                                                                                                                                        |
| B4  | Connected `/metrics` relies only on network placement.                                                                                                                                                                 | Connected runtime requires a header-safe metrics bearer whose value cannot reuse any configured credential secret, compares fixed-length token digests, and marks both successful and rejected projections `no-store`; deployment still must restrict network access. Disabled local runtime retains unauthenticated process metrics.                                                                                                                                                                                                                                                                                                                                                                                                                      | A credential that cannot be represented in its transport or that aliases another authority is not an independent capability. Authentication and cache exclusion remain defence in depth and do not turn application auth into a network-isolation claim.                                                   |
| B5  | Only body-bearing routes have a middleware-owned absolute deadline.                                                                                                                                                    | Every non-liveness route in connected runtime has one owner-selected absolute deadline and typed expiry result; disabled diagnostic runtime retains only its dependency-free health projections.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Client abort does not bound retained server work, while a disabled process has no connected work or connected timeout owner to model.                                                                                                                                                                      |
| B6  | Session lookup, readiness probing, and workflow discovery have no dedicated no-queue bulkhead.                                                                                                                         | Each expensive class has an explicit finite local bulkhead; saturation fails immediately with a typed result. One capability-owned workflow-discovery admission is shared by direct discovery and proposal review, while route-local admission may only shed work earlier.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | A local process must preserve work-class isolation even when distributed fairness is external, and every entry point to the same expensive capability must consume the same resource authority.                                                                                                            |
| B7  | Body lanes are path-local only.                                                                                                                                                                                        | A request must acquire both its path lane and one process-wide weighted retained-body reservation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | The sum of independent maxima must fit one declared process budget.                                                                                                                                                                                                                                        |
| B8  | SQLAlchemy chooses queue-pool defaults.                                                                                                                                                                                | Pool size, overflow, and wait bound are exact admitted settings with no implicit overflow.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Database pressure becomes part of the reviewed capacity model.                                                                                                                                                                                                                                             |
| B9  | Any suffix-shaped asset receives one-year immutable caching.                                                                                                                                                           | Build and runtime pin the bundle-root descriptor plus every bundle-internal ancestor through descriptor-relative no-follow traversal, prove exact paths, sizes, per-file limits, and the aggregate bundle limit before materializing payloads, then require an unchanged final inventory. An unversioned verified asset path returns a `no-store` redirect into the exact SHA-256 manifest namespace. Only a startup-verified member under that content-addressed namespace receives immutable caching; GET and HEAD expose identical representation metadata without a HEAD body, weak and wildcard validators across every repeated `If-None-Match` field line use HTTP conditional-request semantics, and all other responses are no-store or rejected. | Cache lifetime and validators must follow identity across deployments, admitted metadata must bound memory before payload reads, ancestor replacement must not redirect admission, and HEAD must not transfer representation bytes.                                                                        |
| B10 | Invalid frontend timeout values fail inconsistently across clients.                                                                                                                                                    | A shared admission function rejects non-finite, non-integer, non-positive, or platform-excessive timeout values before fetch; every public client maps transport failures consistently.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | One runtime contract must have one failure algebra.                                                                                                                                                                                                                                                        |
| B11 | Frontend test coverage is unmeasured and backend coverage is aggregate-only.                                                                                                                                           | Risk-owned coverage and mutation witnesses are blocking for the owners they claim; coverage evidence itself is bounded, regular-file-only, and arithmetically consistent, without treating line coverage as adequacy. A frontend mutant is killed only when bounded machine-readable Vitest evidence proves that tests executed and failed by assertion; process exit alone is insufficient.                                                                                                                                                                                                                                                                                                                                                               | Evidence must be admissible and sensitive to owner-relevant faults before a percentage or process status can support a claim.                                                                                                                                                                              |
| B12 | A declared frontend response `Content-Length` is used only as an upper-bound precheck.                                                                                                                                 | Every caller-selected bound is at most the 32 MiB platform maximum. For an unencoded response, a supplied `Content-Length` must be canonical and equal the decoded body length; malformed, duplicate-shaped, oversized, short, or long declarations are rejected. Encoded responses use the bounded no-length path and the materialized decoded response removes obsolete `Content-Encoding` and `Content-Length`. Stream cleanup is best-effort and cannot delay or replace an already classified outcome.                                                                                                                                                                                                                                                | Preallocation is sound only when the declared representation length binds retained bytes, decoded bytes must not retain wire-representation metadata, and cleanup is not an authority over transport completion.                                                                                           |
| B13 | Capacity evidence is an untyped digest inside a production-admission receipt.                                                                                                                                          | An offline verifier returns only `qualified` or a closed `not-qualified` reason for one canonical, externally signed capacity receipt whose model, encoder, and decoder share exact resource bounds and whose exact envelope digest can be used as deployment evidence. Runtime startup remains unchanged and no in-process signer is added.                                                                                                                                                                                                                                                                                                                                                                                                               | A digest is an identity, not evidence that the referenced measurements are complete, current, signed, representable, or within budget.                                                                                                                                                                     |
| B14 | Audit replay can verify a local chain but cannot produce an externally authenticatable tip or a machine-checkable retention prerequisite.                                                                              | A verified replay may produce one content-addressed checkpoint candidate with a positive JSON-safe retained sequence; a separate signature admission applies one clock-skew interval consistently to acceptance and returned validity; a pure retention assessment requires that checkpoint, a verified non-empty successor, an exact per-copy storage inventory, byte-identical restore evidence for one retained backup, and matching qualified capacity evidence. It returns no mutation capability.                                                                                                                                                                                                                                                    | Evidence for safe retention must exist before destructive authority; a value object must be representable by its own canonical codec; accepted evidence must be immediately usable under the same trusted time, and a restore record that does not reproduce its selected backup proves no recoverability. |
| B15 | Connected settings admit operator bearer values containing ASCII control characters even though the HTTP credential boundary cannot represent them reliably, and authentication compares variable-length secret bytes. | The existing 32 to 4,096 byte bootstrap credential is restricted to visible ASCII in both settings and request admission, and authentication compares fixed-length SHA-256 digests; its authority and deployment-owned lifecycle are unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | A startup-valid credential must have at least one transport representation that can authenticate, while a fixed-length comparison removes avoidable length-sensitive secret handling; changing expiry, rotation, or human identity semantics remains outside this repair.                                  |

## 5. Finding Closure Ledger

|  # | Disposition | Owner decision                                                                                                                                                                                                                                                                                                                                  |
|---:|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|  1 | qualify     | Keep process-local OAuth protection. Require a distributed ingress abuse-control receipt over trusted edge source identity; never trust caller-supplied forwarding headers in the application.                                                                                                                                                  |
|  2 | qualify     | Bind replica count and distributed rate-control policy into deployment evidence. Local buckets intentionally scale per process and make no replica-wide claim.                                                                                                                                                                                  |
|  3 | adopt       | Add a no-queue session-lookup bulkhead and bounded saturation response before database acquisition.                                                                                                                                                                                                                                             |
|  4 | adopt       | Coalesce concurrent readiness work and bound public admission; one request wave must not multiply DB or JWKS probes.                                                                                                                                                                                                                            |
|  5 | repair      | Include lock acquisition inside the readiness deadline. No waiter may exceed the declared bound merely by waiting for the single-flight owner.                                                                                                                                                                                                  |
|  6 | adopt       | Apply B3; keep exact dependency classification private.                                                                                                                                                                                                                                                                                         |
|  7 | adopt       | Apply B4 and require network restriction in deployment evidence.                                                                                                                                                                                                                                                                                |
|  8 | adopt       | Apply B5 with owner-specific timeout bodies and no retryability claim.                                                                                                                                                                                                                                                                          |
|  9 | adopt       | Isolate workflow discovery with a dedicated bulkhead that leaves provider capacity for other capabilities. Cross-tenant fairness remains an ingress/provider obligation.                                                                                                                                                                        |
| 10 | adopt       | Apply B7. The weighted budget reserves the route maximum when length is absent and the exact admitted length otherwise.                                                                                                                                                                                                                         |
| 11 | qualify     | Optimize the common single-chunk path and account for the unavoidable transient copy factor in capacity qualification. Do not claim a zero-copy ASGI/FastAPI path.                                                                                                                                                                              |
| 12 | adopt       | Apply B8. Values remain environment-qualified, not asserted optimal from static analysis.                                                                                                                                                                                                                                                       |
| 13 | repair      | Apply B2 at the outer HTTP boundary.                                                                                                                                                                                                                                                                                                            |
| 14 | repair      | OAuth callback emits the same bounded private diagnostic before returning its existing redacted result.                                                                                                                                                                                                                                         |
| 15 | repair      | Named omission-safety boundaries catch ordinary `Exception` because an unknown planning failure cannot authorize selected execution; they retain the existing FullCI or withheld outcome and emit one fixed-cardinality stage/reason diagnostic. Cancellation and `BaseException` propagate, and no other boundary gains broad-catch authority. |
| 16 | repair      | Telemetry remains non-authoritative but exposes a bounded internal failure counter/fallback record instead of silent disappearance.                                                                                                                                                                                                             |
| 17 | repair      | Bound logging depth, node count, collection width, scalar bytes, and final record bytes; cycles and unsupported values become deterministic placeholders.                                                                                                                                                                                       |
| 18 | repair      | Use closed event fields where possible and defence-in-depth exact secret-name normalization including password, passphrase, cookie, session, verifier, authorization-code, and closed compound credential aliases such as API key, client secret, private key, and refresh token.                                                               |
| 19 | repair      | Reject caller-owned reserved metadata and write service metadata last.                                                                                                                                                                                                                                                                          |
| 20 | repair      | Reject non-finite floats and serialize with strict RFC 8259 JSON settings.                                                                                                                                                                                                                                                                      |
| 21 | repair      | Generate and verify a canonical asset manifest containing exact path, size, and SHA-256. Regex is not content proof.                                                                                                                                                                                                                            |
| 22 | repair      | Apply B9 and hold one admitted immutable in-memory asset snapshot rather than reading mutable files after composition.                                                                                                                                                                                                                          |
| 23 | repair      | Apply B1. `Retry-After` is emitted only when an owner can state an honest finite retry bound; this design does not invent one.                                                                                                                                                                                                                  |
| 24 | qualify     | Use Content-Length preallocation when admitted and measure the no-length fallback in browser capacity qualification. Streaming JSON is rejected until it is strictly simpler than pagination or bounded buffering for this schema.                                                                                                              |
| 25 | repair      | Apply B10.                                                                                                                                                                                                                                                                                                                                      |
| 26 | retain      | Refuted on the frozen baseline: every production workbench caller already admits scope before invoking the client. Duplicating admission inside a lower transport helper would add no new trust transition.                                                                                                                                     |
| 27 | adopt       | Add blocking frontend coverage for risk-owned code, with explicit exclusions for generated and declarative projections.                                                                                                                                                                                                                         |
| 28 | adopt       | Add a bounded frontend mutation witness for admission, identity, and state-transition kernels; do not mutate rendering boilerplate for a vanity score.                                                                                                                                                                                          |
| 29 | adopt       | Add per-owner and changed-critical backend coverage floors plus mutation witnesses for high-risk pure logic. Aggregate coverage remains informational.                                                                                                                                                                                          |
| 30 | retain      | Chromium is the currently owned support set. Firefox/WebKit support is a separate product decision because it adds support and CI obligations, not a correctness repair.                                                                                                                                                                        |
| 31 | retain      | The static bearer remains an explicitly bounded bootstrap/break-glass mechanism. Normal human use is browser identity. TTL or rotation semantics require a separately owned machine-identity design; silently changing this credential contract is forbidden.                                                                                   |
| 32 | retain      | Do not persist GitHub refresh tokens merely because the provider offers them. The current bounded re-login trades availability for a smaller long-lived credential surface; changing that trade requires an explicit session-lifecycle decision.                                                                                                |
| 33 | retain      | GitHub does not provide an atomic governance snapshot for this read sequence. Preserve best-effort evidence and keep enforcement/compliance unknown.                                                                                                                                                                                            |
| 34 | retain      | Remote reusable-workflow acquisition is a planned product capability, not a defect in the explicitly local-only discovery contract. It requires its own trust and recursion design.                                                                                                                                                             |
| 35 | retain      | Provider inventory remains exact only for fields the provider exposes. Unknown permissions or enterprise relations must stay unknown.                                                                                                                                                                                                           |
| 36 | qualify     | Add an exportable content-addressed chain-tip checkpoint contract; only an externally authenticated retained checkpoint can close truncation/rewrite resistance.                                                                                                                                                                                |
| 37 | qualify     | Define measurable storage, replay, archive, backup, and restore budgets. Retention may compact only behind an externally retained checkpoint and verified successor chain.                                                                                                                                                                      |
| 38 | qualify     | Define a canonical `CapacityQualified` evidence profile and bind its digest into deployment evidence for the exact artifact, environment, replica topology, workload, and resource limits. Static tests cannot mint it.                                                                                                                         |

## 6. Owner Designs

### 6.1 HTTP admission

Admission is hierarchical:

```text
RequestAdmitted(r) iff
  UvicornSlot(r)
  and RouteDeadline(r)
  and ClassBulkhead(r)
  and (
    not BodyBearing(r)
    or PathBodyLane(r) and WeightedBodyReservation(r)
  )
```

All admissions are no-queue unless an owner proves a finite queue budget. A
rejected admission performs no database or provider I/O. Release is exactly
once under success, exception, timeout, disconnect, and cancellation. Workflow
discovery owns one shared reader admission that covers direct discovery and
proposal review before either can request a provider credential or blob; an
HTTP-route limiter is only an earlier rejection optimization and cannot prove
the capability-wide bound.

The liveness route is exempt because an overloaded process must still expose
process existence. Readiness may be coalesced but never cached beyond its
owner-declared freshness bound.

### 6.2 Structured diagnostics

One sanitizer owns recursive traversal. Its output algebra is closed to strict
JSON values and deterministic truncation markers. It must satisfy:

```text
Emitted(record) =>
  StrictJson(record)
  and Bytes(record) <= MaxRecordBytes
  and Nodes(record) <= MaxNodes
  and Depth(record) <= MaxDepth
  and SystemMetadataUnforgeable(record)
  and NoAdmittedSecretField(record)
```

An instrumentation failure cannot alter the domain result. The logger may emit
one bounded fallback diagnostic through the standard library logger, but must
not recursively call itself.

### 6.3 Frontend asset identity

Build output contains one canonical manifest, not served as an asset:

```text
Asset := {contentType, path, sha256, sizeBytes}
Manifest := {schema, files[]}
```

Paths are canonical, relative, unique, regular, and symlink-free. Build and
runtime pin the root descriptor and every bundle-internal ancestor through
inventory and payload reads. Before reading any asset payload, they independently
prove the complete path and size inventory, every per-file limit, and the
aggregate bundle limit. They require the final inventory to remain unchanged. They
then recompute every digest under the already admitted exact size. Runtime
reads every admitted asset once at composition and serves those immutable bytes
only. Unversioned asset references redirect without caching
into a namespace equal to the canonical manifest SHA-256. Therefore:

```text
ServedImmutable(path, bytes)
  => path = /assets/_bundle/SHA256(manifest)/relativePath
  and Manifest(relativePath, size(bytes), SHA256(bytes))
```

The manifest does not prove build provenance or SRI in an external CDN. Those
claims remain with release publication and browser transport respectively.

### 6.4 Capacity qualification

Static configuration owns dimensions; environment evidence owns adequacy:

```text
CapacityQualified iff
  ExactArtifact
  and ExactCapacityProfile
  and ExactReplicaTopology
  and ExactResourceLimits
  and FrozenWorkloadDigest
  and BoundedMeasurements
  and EveryHardBudgetSatisfied
  and SignedByCapacityOwner
```

The hard budget set includes retained and transient body bytes, frontend
response memory, event-loop latency, DB pool wait, provider bulkhead wait,
readiness latency, shutdown, audit growth, replay temporary storage, and
fallback completion. A signed result for another epoch is irrelevant.

The receipt protocol is intentionally offline:

```text
CapacityQualified(receipt, expectation) iff
  CanonicalEnvelope(receipt)
  and ValidExternalSignature(receipt)
  and ExactCapacityProfile(receipt.profileDigest)
  and ExactIdentity(receipt, expectation)
  and CompleteMetricDomain(receipt.measurements)
  and EveryObservedValueWithinSignedBudget(receipt.measurements)
  and ValidityIntervalContainsTrustedNow(receipt)
```

`CapacityQualified` is evidence, not deployment authority. The runtime does not
load or require the receipt unless a later separately approved production mode
binds it. The repository does not contain a capacity private key or a function
that turns local measurements into truth.

### 6.5 Audit checkpoint and retention prerequisite

Checkpoint production starts only from a complete valid replay scan. The
candidate binds the exact audit schema, database identity, artifact, terminal
sequence, event identity and hash, and observation time. External admission
authenticates the candidate bytes without claiming that another system retained
them.

```text
RetentionPrerequisiteVerified iff
  AuthenticatedCheckpoint
  and NonEmptySuccessorExtendsCheckpoint
  and PerCopyInventoryBindsCheckpointAndSuccessor
  and CapacityReceiptBindsExactInventory
  and StorageDimensionsFitQualifiedBudgets
```

The result is deliberately a value, not an opaque mutation capability. No
current archive, compaction, deletion, or truncation operation consumes it, so
this tranche cannot make destructive retention newly reachable.

## 7. Rejected Alternatives

1. `X-Forwarded-For` application throttling: rejected because the runtime
   deliberately distrusts forwarded headers and cannot identify the trusted
   edge hop.
2. One global limiter for every route: rejected because cheap health traffic
   and expensive provider/DB traffic have different budgets and failure costs.
3. A logging `default=str` serializer: rejected because it can invoke
   attacker-controlled or secret-bearing object representations.
4. Filename regex plus long cache: rejected because it admits same URL with
   different bytes.
5. Full refresh-token persistence: rejected because it adds long-lived secret
   authority without an adopted availability requirement.
6. Claiming point-in-time GitHub state: rejected because no local algorithm can
   strengthen the provider's read consistency contract.
7. Whole-repository mutation and coverage percentages: rejected because they
   optimize scores rather than risk-owned oracle sensitivity.

## 8. Acceptance And Falsifiers

Implementation is acceptable only if all of the following hold:

- every `repair` and `adopt` row has a requirement binding and a witness that
  kills its named counterexample;
- every `qualify` row has an executable profile, producer/verifier boundary,
  and an explicit `not qualified` result when evidence is absent;
- every `retain` row remains an explicit non-claim or roadmap capability;
- the business-delta table is complete relative to the code diff;
- static import, ownership, documentation graph, lint, type, generated
  contract, and Proofkit admission gates pass;
- GitHub executes all behavioral, integration, browser, mutation, coverage,
  container, and full-check witnesses on the exact candidate commit; and
- a fresh independent review of the frozen candidate produces no new
  reproducible current-diff P0-P2 finding.

The following remain non-claims after local implementation: live capacity,
replica-wide fairness, trusted ingress identity, production scrape and alert
delivery, external checkpoint custody, provider snapshot isolation, remote
reusable-workflow safety, and production omission authority.
