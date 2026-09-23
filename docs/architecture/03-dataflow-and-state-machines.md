# Dataflow And State Machines

Status: runtime flow specification

Last verified: 2026-08-31

## 1. Purpose

This document owns CI Coordinator's high-level dataflow and critical state
machines. Module internals are owned by `modules/*.md`.

## 2. Plan Request Dataflow

```mermaid
flowchart TD
  EVENT["GitHub event in an adopted static workflow"] --> TOKEN["Request OIDC token"]
  TOKEN --> REQUEST["POST plan request to FastAPI edge"]
  REQUEST --> IDENTITY["Verify OIDC and workflow identity"]
  IDENTITY --> IDRESULT{"Identity result"}
  IDRESULT -- "invalid" --> REJECT["Reject request; return no plan"]
  IDRESULT -- "dependency unavailable" --> UNAVAILABLE["Typed unavailable; return no plan"]
  REJECT --> LOCAL["Target-owned local FullCI fallback when available"]
  UNAVAILABLE --> LOCAL
  IDRESULT -- "admitted" --> CONFIG["Load active config epoch"]
  CONFIG --> CONTEXT["Build diff and dependency-graph context"]
  CONTEXT --> COMPLETE{"Input complete and fresh?"}
  COMPLETE -- "no" --> FALLBACK["Signed FullCI fallback"]
  COMPLETE -- "yes" --> PLAN["Deterministic planner"]
  PLAN --> VERIFY["Verifier recomputes plan"]
  VERIFY --> ADVICE{"Agent advice monotonic?"}
  ADVICE -- "no" --> IGNORE["Reject advice"]
  ADVICE -- "yes" --> ESCALATE["Escalate plan only"]
  IGNORE --> SHARDS["Runner shard optimizer"]
  ESCALATE --> SHARDS
  SHARDS --> AUTH{"Exact production authority binds candidate?"}
  AUTH -- "no" --> FALLBACK
  AUTH -- "yes" --> SIGN["Sign selected candidate envelope"]
  SIGN --> RECHECK{"Transactional authority, epoch, and controls still valid?"}
  RECHECK -- "no" --> FALLBACK
  RECHECK -- "yes" --> PERSIST["Persist selected envelope with authority foreign key"]
  PERSIST --> RESPONSE["Return retained signed envelope"]
  FALLBACK --> RESPONSE
  RESPONSE --> VALIDATE["Bootstrap validates envelope"]
  VALIDATE --> EXECUTE{"Valid selected plan?"}
  EXECUTE -- "yes" --> SELECTED["Selected matrix or reusable workflow"]
  EXECUTE -- "no" --> FULL["FullCI"]
  SELECTED --> AGGREGATE["Aggregator with always-run semantics"]
  FULL --> AGGREGATE
  AGGREGATE --> GATE["Stable required gate"]
  AGGREGATE --> OBSERVE["Observation and audit replay"]
```

The local fallback after an invalid credential or unavailable identity provider
is owned by the adopted target workflow. It is not a server-signed plan. A
server-signed FullCI envelope exists only after request identity is admitted.

## 3. Config Admission And Epoch State

Admission is a pure terminating flow. Failed input creates no durable candidate:

```mermaid
flowchart LR
  A["Bounded source bytes"] --> B["Strict UTF-8 decode"]
  B --> C["Strict JSON or YAML 1.2 parse"]
  C --> D["Structural validation"]
  D --> E["Semantic validation"]
  E --> F["Producer feasibility"]
  F --> G["Canonical compilation"]
  G --> H["Immutable validated epoch draft"]
  A -. "failure" .-> X["One stable diagnostic; no epoch"]
  B -. "failure" .-> X
  C -. "failure" .-> X
  D -. "failure" .-> X
  E -. "failure" .-> X
  F -. "failure" .-> X
  G -. "failure" .-> X
```

Epoch lifecycle is owned by the implemented config-epoch, persistence, and
operator-control specifications rather than policy admission:

```mermaid
stateDiagram-v2
  [*] --> Candidate
  Candidate --> Rejected: validation failed
  Candidate --> Validated: admission passed
  Validated --> Active: compare-and-swap active pointer
  Active --> Superseded: newer epoch activated
  Superseded --> Active: rollback activation after revalidation
  Rejected --> [*]
```

Rules:

```text
Candidate is a transient admission state, not a durable entity.
Policy admission creates only an immutable validated draft and no durable candidate.
Existing epoch is immutable.
Active pointer changes only transactionally.
Rollback activates an epoch through a new audited transition.
Every plan uses exactly one config epoch snapshot.
```

## 4. Plan State Machine

```mermaid
stateDiagram-v2
  [*] --> Requested
  Requested --> Admitted: identity verified
  Requested --> Rejected: invalid request or identity; no plan
  Requested --> Unavailable: identity dependency unavailable; no plan
  Admitted --> ContextBuilt: diff, graph, catalog, config resolved
  Admitted --> SignedFallback: context stale, unknown, incomplete, or invalid
  ContextBuilt --> Planned: deterministic plan computed
  Planned --> Verified: verifier accepts recomputed plan
  Planned --> SignedFallback: verifier rejects
  Verified --> SignedSelected: enforcement gate allows selected execution
  Verified --> SignedFallback: enforcement disabled or rollout gate blocks
  SignedSelected --> Observed
  SignedFallback --> Observed
  Observed --> Replayed
  Rejected --> [*]
  Unavailable --> [*]
```

## 5. Production Admission And Issuance

Startup authority flow:

```mermaid
flowchart TD
  A["Enforcing settings candidate"] --> B["Load packaged production build identity"]
  B --> C["Stable bounded receipt read"]
  C --> D["Canonical JSON and Ed25519 admission"]
  D --> E{"Artifact, source, environment, rollout, scopes, evidence, and TTL exact?"}
  E -- "no" --> R["Reject startup; allocate no provider client"]
  E -- "yes" --> F["Create opaque grant and immutable registration"]
  F --> G["Create PostgreSQL engine"]
  G --> H["Register authority and scope bindings; exact readback"]
  H --> I{"Committed without expiry or conflict?"}
  I -- "no" --> J["Rollback, dispose engine, reject startup"]
  I -- "yes" --> K["Allocate GitHub and JWKS clients"]
  K --> L["Compose enforcing runtime"]
```

Request-time selected issuance flow:

```mermaid
flowchart TD
  A["Verified plan, shard plan, authenticated workflow"] --> B["Match exact receipt subject"]
  B --> C{"In-memory grant still valid?"}
  C -- "no" --> F["Sign and persist FullCI"]
  C -- "yes" --> D["Sign selected candidate and create opaque guard"]
  D --> E["Lock repository scope in READ COMMITTED transaction"]
  E --> G{"Registered and unexpired by DB clock; epoch active; controls clear?"}
  G -- "no" --> F
  G -- "yes" --> H["Insert selected envelope and authority foreign key"]
  H --> I["Commit exact replayable issuance"]
```

The first flow authenticates one process-lifetime capability. The second
linearizes every durable selected effect against current database-owned facts.
Neither flow implies that the external signer followed its evidence procedure.

### 5.1 Dormant Target-Authority Evidence Retention

```mermaid
flowchart TD
  B["Phase-0 baseline"] --> T["Apply owner-approved transition"]
  D["Transition delta"] --> T
  T --> U["Expected adapted relation"]
  M["Stable workflow manifest"] --> O["Observation producer"]
  S["Exact source binding"] --> O
  OC["Observation candidates"] --> O
  P["Owner projection policy"] --> O
  RC["Registration candidates"] --> R["Registration projection"]
  P --> R
  U --> R
  R --> C["Close exact full-row relation"]
  O --> C
  C --> E["Assemble exact 13-role bundle"]
  B --> E
  D --> E
  U --> E
  M --> E
  S --> E
  RC --> E
  OC --> E
  P --> E
  E --> V["Strict owner-codec and semantic replay"]
  V --> F["Digest-addressed offline file"]
  V -. "no authority edge" .-> X["Selected execution"]
```

Publication is a separate mechanism state machine:

```mermaid
stateDiagram-v2
  [*] --> Absent
  Absent --> StagedPrivate: bounded no-follow input admitted
  StagedPrivate --> Flushed: complete write and file fsync
  Flushed --> Linked: atomic no-overwrite hard link
  Linked --> Durable: parent directory fsync
  Durable --> Published: temporary unlink and final directory fsync
  Absent --> Rejected: input or path rejected
  StagedPrivate --> Rejected: write or fsync failed
  Flushed --> Rejected: collision or link failed
  Linked --> Rejected: durability or cleanup uncertain
  Rejected --> [*]
```

Only `Published` returns an affirmative offline receipt. A complete final file
left by a crash remains inert evidence and is never interpreted as production
authority.

## 6. Database Compatibility State Machine

Schema history and deployment lifecycle are orthogonal. A database revision
transition changes the declared capability state; a deployment event changes
which binaries execute. Neither event is silently projected onto the other.

Revision-chain machine:

```mermaid
stateDiagram-v2
  [*] --> DeclaredRevision
  DeclaredRevision --> DeclaredRevision: expand successor
  DeclaredRevision --> DeclaredRevision: contract successor
  DeclaredRevision --> DeclaredAncestor: pre-retention downgrade selection
  DeclaredAncestor --> DeclaredRevision: exact replay of known successor
  DeclaredRevision --> Failed: invalid successor or retained-capability attestation
  DeclaredAncestor --> Failed: unknown ancestor or replay mismatch
  Failed --> [*]
```

Deployment-lifecycle machine:

```mermaid
stateDiagram-v2
  [*] --> OldOnly
  OldOnly --> Mixed: deploy new binary
  Mixed --> NewOnly: retire old binary
  Mixed --> OldOnly: code rollback within current capability frontier
  NewOnly --> Mixed: restore admitted old binary
  OldOnly --> Failed: old requirements not admitted
  Mixed --> Failed: either binary requirements not admitted
  NewOnly --> Failed: new requirements not admitted
  Failed --> [*]
```

The valid system state is the product of these machines subject to the profile
admission predicate. A schema transition does not imply deployment progress,
and code rollback does not create a schema transition.

Application transaction flow:

```mermaid
flowchart LR
  A["Configure READ COMMITTED"] --> B["Begin and verify isolation"]
  B --> C["Set local deadlines"]
  C --> D["Acquire shared compatibility fence"]
  D --> E["Read current head in next statement"]
  E --> F{"Schema, data, and privilege attestation valid?"}
  F -- "no" --> G["Rollback; expose no repository"]
  F -- "yes" --> H["Execute schema-dependent operation"]
  H --> I["Commit or roll back"]
```

Bounded workbench snapshot flow:

```mermaid
flowchart LR
  A["Configure READ COMMITTED"] --> B["Begin; set and verify READ ONLY"]
  B --> C["Acquire shared compatibility fence"]
  C --> D["Admit complete read capability set"]
  D --> E["One statement: bounded sections and metadata"]
  E --> F["Decode, integrity-check, and redact"]
  F --> G["Roll back read-only transaction"]
```

Online migration flow acquires the same key in exclusive mode at the Alembic
environment boundary before head inspection or `run_migrations()`, then commits
DDL, one forward declaration, and Alembic-head movement atomically. Downgrade
selects an existing ancestor without appending. Startup readiness projects the
same admission predicate but never replaces the per-transaction check.

## 7. Provider Inventory Dataflow

```mermaid
flowchart LR
  ADMIN["Keycloak administrator with read role"] --> SESSION["Unexpired opaque server session"]
  SESSION --> APP["GitHub App installation inventory"]
  APP --> DECODE["Strict bounded decoder"]
  DECODE --> CATALOG["Complete or explicit partial installation catalog"]
  CATALOG --> SELECT["Selected active organization installation"]
  SELECT --> PAGE["Bounded user-visible repository page"]
  PAGE --> UI["Exact visible repository shown without mutation authority"]
```

Invariant:

```text
CatalogVisible(actor, repository)
does not imply WorkbenchAuthorized(actor, repository)
does not imply Configured(repository)
does not imply Enforcing(repository)
```

Each provider page is one observation, not snapshot isolation. Contradictory
totals, malformed next-page evidence, installation mismatch, ineligible
repositories, and provider failures remain typed non-success outcomes. A
Keycloak read role and GitHub App installation visibility are both required;
neither supplies repository-review or config-activation authority.

### 7.1 Exact-Commit Workflow Discovery

```mermaid
flowchart LR
  SELECT["Authorized repository selection"] --> AUTH["Exact scope authorization"]
  AUTH --> COMMIT["Resolve or admit exact commit"]
  COMMIT --> TREES["Non-recursive Git tree chain"]
  TREES --> BLOBS["Content-addressed workflow blobs"]
  BLOBS --> PARSE["Bounded non-executing YAML projection"]
  PARSE --> LEDGER["Assertion or unknown for every predicate"]
  LEDGER --> GRAPH["Same-snapshot local call graph"]
  GRAPH --> PROPOSAL["Observe-only admitted proposal or blockers"]
  PROPOSAL --> UI["Discovery progressive disclosure"]
```

Invariant:

```text
AuthorizedExactSnapshot
and ClosedPredicateLedger
and UniqueStaticProviderSignal
and ExistingPolicyAdmission
  => ReviewableObserveOnlyProposal

otherwise => ExplicitUnknownOrBlocker
```

The response is request-scoped and stateless. Review, registration,
activation, provider mutation, and omission authority are absent from this
state machine.

### 7.2 Repository Attestation And Proposal Review

```mermaid
flowchart LR
  CMD["Scope, operation, manifest, and active baseline"] --> SESSION["Keycloak configure role + Origin + CSRF"]
  SESSION --> SCAN0["Re-run current-head discovery"]
  SCAN0 --> BIND["Bind one-use transaction to session + exact proposal"]
  BIND --> OAUTH["GitHub OAuth code + PKCE reviewer step-up"]
  OAUTH --> GRANT["Fresh maintain-or-admin evidence"]
  GRANT --> REPLAY{"Exact operation retained?"}
  REPLAY -- "yes" --> DUP["Return retained acceptance"]
  REPLAY -- "no" --> BASE["Read active epoch and revision"]
  BASE --> SCAN["Reproduce current-head proposal"]
  SCAN --> MATCH{"Manifest reproduced?"}
  MATCH -- "no" --> STOP["Blocked or stale; no write"]
  MATCH -- "yes" --> DIFF["Bounded normalized-policy diff"]
  DIFF --> TX["Scope-locked PostgreSQL transaction"]
  TX --> CHECK{"Pending transaction and active baseline still exact?"}
  CHECK -- "no" --> STOP
  CHECK -- "yes" --> COMMIT["Consume transaction + register epoch + receipt + audit"]
```

Invariant:

```text
KeycloakConfigureAuthority
and OneUseRepositoryReviewerAttestation
and ExactOperationOrFreshCurrentProposal
and ABAResistantActiveBaseline
and BoundedSemanticDiff
  => DuplicateOrAtomicNonActivatingRegistration

otherwise => NoAttemptedDurableEffect
```

The provider observation and database transaction cannot be globally atomic.
The retained record therefore names the exact reviewed revision, transaction,
reviewer, permission, issue time, observation time, and expiry. Activation is a
separate command requiring the Keycloak `activate` role, unchanged proposal
identity, optimistic config concurrency, and a fresh GitHub App recheck of the
retained reviewer's current permission.

### 7.3 Exact Governance Comparison

```mermaid
flowchart LR
  READ["Authenticated comparison read"] --> GRANT["Fresh exact-scope read grant"]
  GRANT --> B0["Read active baseline pointer B0"]
  B0 --> OBS["Fresh bounded governance observation"]
  OBS --> B1["Read active baseline pointer B1"]
  B1 --> STABLE{"B0 equals B1?"}
  STABLE -- "no" --> STALE["Stale; no comparison"]
  STABLE -- "yes, absent" --> NONE["Unbaselined observation"]
  STABLE -- "yes, active" --> BYTES["Compare canonical state bytes"]
  BYTES --> RESULT["Matches or differs + exact coordinate and set delta"]
```

Invariant:

```text
FreshReadAuthority
and StableExactBaselinePointer
and FreshBestEffortObservation
and SameRepositoryScope
  => UnbaselinedOrExactByteRelation

otherwise => TypedNonSuccess
```

The comparison excludes observation time and uses complete canonical rule bytes
as set members. It does not pair two unequal rules as one semantic
modification. `matches` is byte equality against one approved expected state,
not compliance, provider snapshot truth, enforcement, release readiness, or
omission authority.

### 7.4 Dormant Target-Authority Producers

```mermaid
flowchart TB
  GIT["Exact commit, trees, and regular blobs"] --> W["Stable workflow manifest + source binding"]
  DISC["Same-revision workflow discovery"] --> OBS["Independent observation enumeration"]
  W --> OBS
  GOV["Subject-bound best-effort provider evidence"] --> OBS
  ART["Closed policy, catalog, registry, and consumer-lab epoch"] --> OBS
  ART --> REG["Owner declaration enumeration"]
  BASE["Phase-0 baseline"] --> U["Apply owner-approved transition"]
  DELTA["Owner transition delta"] --> U
  U --> REG
  POLICY["Owner projection policy"] --> REG
  POLICY --> PROJECT["Total candidate projection"]
  OBS --> PROJECT
  REG --> R["Registration inventory"]
  PROJECT --> O["Observation inventory + raw domain + ledger"]
  R -. "later exact comparator" .-> CLOSE["Unactivated relation closure"]
  O -. "later exact comparator" .-> CLOSE
```

```text
RegistrationKeys derive from U.
ObservationKeys derive from Git, discovery, target artifacts, and provider evidence.
ObservationEnumeration does not consume B, D, U, R, or their keys.
Any unknown or mismatch prevents later relation closure.
```

The current flow is pure and dormant. It does not capture a production
baseline, persist evidence, sign a receipt, fence a cutover generation, or
activate omission.

## 8. Runner Shard Dataflow

```mermaid
flowchart LR
  A["Verified selected validation set"] --> B["Test manifest"]
  C["Runner snapshot"] --> D["Capacity bound"]
  E["Historical duration observations"] --> F["Cost model"]
  B --> G["Shard optimizer"]
  D --> G
  F --> G
  G --> H["ShardPlan"]
  H --> I["Execution matrix"]
  H --> J["Audit event"]
```

Invariant:

```text
Runner capacity can alter shard count and max parallelism.
Runner capacity cannot alter the selected validation set.
```

## 9. Audit Replay Flow

```mermaid
flowchart TD
  A["Subject id"] --> B["Load audit events"]
  B --> C["Verify hash chain before filtering"]
  C --> D{"Chain valid?"}
  D -- "no" --> E["Replay failed"]
  D -- "yes" --> F["Rebuild subject inputs"]
  F --> G["Recompute plan or projection"]
  G --> H{"Expected output matches?"}
  H -- "yes" --> I["Replay passed"]
  H -- "no" --> J["Replay mismatch"]
```

Hash-chain verification must happen before subject filtering. Otherwise a
corrupted event outside the filtered view could be hidden from replay.
