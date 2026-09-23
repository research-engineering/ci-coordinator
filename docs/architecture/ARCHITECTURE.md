# Architecture Overview

Status: derived visual projection

Last verified: 2026-08-30

## 1. Purpose and Authority

This document is the progressive-disclosure entry point for CI Coordinator's
architecture. It owns no durable product invariant. Each diagram projects facts
owned by the specification named below it. If a projection conflicts with an
owner, the owner is authoritative and this overview is defective.

That rule prevents a visual guide from becoming a second, drifting
specification:

```text
OneFact -> OneOwner
OverviewFact -> Reference(OwnerFact)
Conflict(OverviewFact, OwnerFact) -> Reject(OverviewFact)
```

The proof graph starts with [premises](00-system-axioms.md), adopted
[system laws](01-meta-specification.md), and the current
[context map](02-context-map.md). Dataflow, module contracts, and cross-cutting
contracts then contribute explicit acyclic edges into machine requirements and
executable witnesses; they are not a universal total order. The authoritative
graph is routed by the [specification index](INDEX.md).

## 2. System Context

CI Coordinator is a control plane between GitHub evidence and repository-owned
CI. It may select or shape work only from admitted evidence. A missing,
ambiguous, stale, or contradictory input maps to FullCI.

```mermaid
flowchart LR
  DEVELOPER["Developer or merge queue"] --> GH["GitHub"]
  WORKFLOW["Adopted target workflow"] -->|"OIDC-bound plan request"| API["CI Coordinator API"]
  GH -->|"signed webhook deliveries"| API
  API -->|"app- or installation-authenticated bounded reads"| GHAPI["GitHub REST API"]
  API -->|"JWKS retrieval"| OIDC["GitHub Actions OIDC"]
  API --> DB[("PostgreSQL 18.6")]
  ADMIN["Keycloak administrator"] -->|"catalog, configuration, and review"| API
  BOT["Keycloak workload"] -->|"role-scoped API operations"| API
  REVIEWER["GitHub repository manager"] -->|"proposal-bound attestation"| API
  BREAKGLASS["Break-glass operator"] -->|"scoped fail-safe controls"| API
  API -->|"signed FullCI or admitted selected plan"| WORKFLOW
  WORKFLOW -->|"repository-owned commands"| RUNNERS["GitHub-hosted or self-hosted runners"]
  UI["Operator repository portfolio"] -->|"same-origin session; CSRF on review"| API
```

Current boundary: the service is runnable in disabled, non-enforcing, or
receipt-gated enforcing mode. It never centrally dispatches runner work or
publishes coordinator success; the target workflow remains the execution owner.
Enforcing mode may issue a selected matrix only for the exact durably registered
production authority and request-time transactional guard. The current control
plane uses Keycloak human and workload identity, GitHub App provider reads, and
one-use GitHub reviewer attestation for proposal review and explicit activation.
It also provides repository proof projection, exact-commit workflow discovery,
expected-governance baseline approval, and read-only governance comparison.
Review, activation, baseline approval, and comparison remain separate
capabilities; none mutates the provider, centrally dispatches work, proves
current compliance, or independently grants omission authority.

Owners: [system laws](01-meta-specification.md),
[GitHub Actions semantics](cross-cutting/github-actions-semantics.md), and
[runtime composition](modules/runtime-composition.md). The admitted architecture
style and future same-store UI CQRS are owned by the
[backend architecture decision](../decisions/backend-architecture-style.md).

## 3. Trust Boundaries

Every external byte is untrusted until its owning admission function succeeds.
Secrets are consumed only by the composition root and are absent from domain
contracts, error projections, and logs.

```mermaid
flowchart LR
  subgraph U["Untrusted external zone"]
    WH["Webhook bytes and headers"]
    PR["Plan request and OIDC token"]
    OR["Keycloak token or break-glass bearer"]
    BR["Keycloak/reviewer callback, cookie, origin, and CSRF proof"]
    GR["GitHub API responses"]
    CF["Repository policy document"]
    PA["Production admission envelope"]
  end

  subgraph A["Admission boundary"]
    HMAC["HMAC before JSON parsing"]
    OIDCVERIFY["RS256, JWKS, issuer, audience, run binding"]
    OPSCOPE["Credential-plane, role, and repository-scope admission"]
    BIDENT["Keycloak OAuth, token, opaque-session, origin, and CSRF admission"]
    BGRANT["One-use reviewer and GitHub App repository evidence"]
    DECODE["Bounded provider decoders"]
    CONFIG["Schema, semantic, and feasibility admission"]
    PROD["Canonical Ed25519 admission and build binding"]
  end

  subgraph T["Trusted process zone"]
    EVIDENCE["Typed immutable evidence"]
    PLAN["Deterministic planning and verification"]
    EFFECTS["Application use cases"]
  end

  subgraph D["Durability zone"]
    PG[("Capability-attested PostgreSQL")]
    AUDIT["Append-only audit chain"]
  end

  WH --> HMAC --> EVIDENCE
  PR --> OIDCVERIFY --> EVIDENCE
  OR --> OPSCOPE --> EFFECTS
  BR --> BIDENT --> BGRANT --> EFFECTS
  GR --> DECODE --> EVIDENCE
  CF --> CONFIG --> EVIDENCE
  PA --> PROD --> EVIDENCE
  EVIDENCE --> PLAN --> EFFECTS --> PG
  EFFECTS --> AUDIT --> PG
```

Owners: [security and OIDC](cross-cutting/security-and-oidc.md),
[identity admission](modules/identity-admission.md),
[config control](modules/config-control.md),
[GitHub integration](modules/github-integration.md), and
[persistence](modules/persistence.md).

## 4. Internal Component Topology

The backend is a contract-first modular monolith. Dependency direction follows
policy ownership, not request flow: infrastructure implements ports owned by
application or domain contexts; domain code never imports transport or
infrastructure.

```mermaid
flowchart TB
  RUNTIME["runtime composition and lifecycle"] --> HTTP["api/http routers and DTOs"]
  HTTP --> APP["application use cases"]

  APP --> ID["identity_admission"]
  APP --> CONFIG["config_control and config_epochs"]
  APP --> INGEST["github_ingestion"]
  APP --> CONTEXT["repo_context"]
  APP --> PLANNER["planning_core"]
  APP --> VERIFY["verification_core"]
  APP --> RISK["agent_risk_advice"]
  APP --> ISSUE["plan_issuance"]
  APP --> CAPACITY["runner_capacity"]
  APP --> EXEC["execution_orchestration"]
  APP --> RECON["reconciliation and shadow_mode"]
  APP --> CONTROL["operator_controls"]
  APP --> AUDIT["audit_replay"]
  APP --> REVIEW["proposal_review"]
  APP --> BASELINE["governance_baseline"]
  APP --> COMPARE["governance_comparison"]
  APP --> CPID["control_plane_identity"]
  APP --> PORTS["context-owned ports"]

  PLANNER --> CONTRACT["validation_contract"]
  VERIFY --> CONTRACT
  CAPACITY --> CONTRACT
  CAPACITY --> EXEC
  EXEC --> CONTRACT
  TARGET["target_artifacts offline adapter"] --> CONTEXT
  TARGET --> CAPACITY
  TARGET --> EXEC

  ID --> KERNEL["kernel"]
  CONFIG --> KERNEL
  INGEST --> KERNEL
  CONTEXT --> KERNEL
  PLANNER --> KERNEL
  VERIFY --> KERNEL
  ISSUE --> KERNEL
  CAPACITY --> KERNEL
  RECON --> KERNEL
  AUDIT --> KERNEL

  INFRA["persistence and integrations/github/keycloak"] -. "implements" .-> PORTS
  RUNTIME --> INFRA
  RUNTIME --> OBS["observability and runtime_settings"]
  RUNTIME --> WORKBENCH["workbench_read_models"]
  RUNTIME --> INVENTORY["provider_inventory"]
  RUNTIME --> GOVERNANCE["governance_observation"]
  RUNTIME --> DISCOVERY["workflow_discovery"]
  HTTP --> WORKBENCH
  HTTP --> INVENTORY
  HTTP --> GOVERNANCE
  HTTP --> BASELINE
  HTTP --> COMPARE
  HTTP --> DISCOVERY
  INVENTORY --> KERNEL
  GOVERNANCE --> CONFIG
  GOVERNANCE --> KERNEL
  BASELINE --> GOVERNANCE
  BASELINE --> KERNEL
  COMPARE --> GOVERNANCE
  COMPARE --> BASELINE
  COMPARE --> KERNEL
  DISCOVERY --> CONFIG
  DISCOVERY --> KERNEL
  REVIEW --> CONFIG
  REVIEW --> KERNEL

  PLANNER -. "forbidden" .-> INFRA
  VERIFY -. "forbidden" .-> HTTP
  CAPACITY -. "forbidden" .-> RECON
  EXEC -. "forbidden" .-> CAPACITY
```

The planner answers what validation is required. The capacity optimizer answers
how already selected validation should be distributed. Their current package
boundary and reverse-import prohibition mechanically prevent one source-level
capacity-to-coverage dependency; requirement-owned behavior witnesses are still
needed to prove that runner scarcity cannot reduce coverage at runtime.

Owners: [context map](02-context-map.md) and the corresponding
[module specifications](INDEX.md#5-module-specifications).

## 5. Dynamic Plan Dataflow

The current runtime has two connected authority paths. Non-enforcing mode
always issues FullCI and retains candidate omissions as shadow evidence.
Enforcing mode may issue selected execution only after startup receipt
admission and durable registration plus a request-time transactional recheck.

```mermaid
sequenceDiagram
  autonumber
  participant W as Target workflow
  participant API as FastAPI edge
  participant ID as Identity admission
  participant CFG as Active config epoch
  participant CTX as Repository context
  participant P as Deterministic planner
  participant V as Plan verifier
  participant C as Capacity optimizer
  participant A as Production authority
  participant I as Plan issuer
  participant DB as PostgreSQL

  W->>API: OIDC token plus run-bound request
  API->>ID: verify token, claims, workflow ref, repository, run
  alt identity or request unavailable
    API-->>W: typed unavailable response
  else trusted identity
    API->>CFG: resolve immutable active epoch
    CFG->>CTX: load bounded diff, workflows, checks, and runner evidence
    CTX->>P: complete planning input or explicit uncertainty
    P->>V: candidate plan plus omission proof
    V->>V: independently recompute and admit equal-or-stronger coverage
    V->>C: verified required checks and shardable work
    C->>I: verified plan plus capacity-shaped execution metadata
    alt non-enforcing or authority mismatch
      I->>DB: atomically persist signed FullCI-safe issuance and audit evidence
    else exact enforcing authority
      I->>A: bind candidate, registry, workflow, scope, and expiry
      A->>DB: transactionally recheck registration, DB time, epoch, and controls
      I->>DB: persist selected envelope with authority foreign key
    end
    I-->>API: replayable signed envelope
    API-->>W: FullCI or admitted selected plan
  end
```

The safety implication is:

```text
IncompleteEvidence or InvalidIdentity or FailedDependency or DisabledEnforcement
  -> FullCI or TypedUnavailable
  -> not ReducedCoverage
```

Owners: [dataflow and state machines](03-dataflow-and-state-machines.md),
[planning core](modules/planning-core.md),
[runner capacity](modules/runner-capacity.md), and
[plan issuance](modules/plan-issuance.md).

### 5.0 Organization Control Plane

The following is the as-built authority topology. Identity proves a principal
and roles; capability owners retain business admission; provider credentials
perform only exact provider operations.

```mermaid
flowchart LR
  HUMAN["Administrator"] -->|"code + PKCE"| KC["deployment-owned Keycloak"]
  BFF -->|"client_secret_basic"| KC
  KC -->|"validated ID token"| BFF["Opaque-session boundary"]
  BOT["Administrative workload"] -->|"client credential"| KC
  KC -->|"access token"| APIID["Machine-token boundary"]
  BFF --> API["Coordinator API"]
  APIID --> API
  API --> OWNER["Capability owner"]
  API -->|"proposal-bound transaction"| REVIEW["Reviewer OAuth boundary"]
  REVIEWER["Repository manager"] -->|"code + PKCE"| REVIEW
  REVIEW -->|"durable receipt"| OWNER
  OWNER -->|"admitted operation"| APP["Organization GitHub App"]
  APP --> INSTALL["Exact installation scope"]
  WORKFLOW["Target workflow"] -->|"Actions OIDC"| PLAN["Plan boundary"]
  GITHUB["GitHub webhook"] -->|"HMAC"| EVENT["Event boundary"]
  BREAKGLASS["Break-glass"] -->|"FullCI or disable omission only"| OWNER
```

Owner: [organization control plane](cross-cutting/organization-control-plane.md).

### 5.1 Provider Inventory Dataflow

The catalog admits a Keycloak human session or workload token with the `read`
role, then uses only deployment-owned GitHub App and installation credentials
for provider reads. No GitHub user token is retained. Catalog visibility remains
independent from repository attestation and action authority.

```mermaid
sequenceDiagram
  participant B as Browser
  participant API as Authenticated read route
  participant ID as Keycloak identity boundary
  participant AUTH as Installation allowlist
  participant APP as GitHub app-JWT transport
  participant INSTALL as Installation-token transport
  participant GH as GitHub REST API

  B->>API: catalog request + opaque session
  API->>ID: authenticate and admit read role
  ID-->>API: immutable Keycloak actor
  API->>AUTH: admit installation before provider I/O
  API->>APP: read exact installation
  APP->>GH: bounded app-authenticated GET
  API->>INSTALL: read exact installation repository page
  INSTALL->>GH: bounded installation-authenticated GET
  GH-->>API: bounded catalog evidence or typed failure
  API-->>B: bounded catalog projection
```

Owner: [provider inventory](modules/provider-inventory.md).

### 5.2 Workflow Discovery Dataflow

```mermaid
sequenceDiagram
  participant B as Browser
  participant API as Authenticated read route
  participant A as Control-plane scope authorization
  participant D as Discovery service
  participant GH as GitHub Git database adapter
  participant P as Static parser and graph
  participant C as Existing config admission

  B->>API: repository scope plus optional exact commit
  API->>A: resolve session and exact read grant
  A-->>API: current user-and-App relation
  API->>D: immutable actor, scope, revision
  D->>D: authorize scope before provider I/O
  D->>GH: read exact repository snapshot
  GH->>GH: repository, ref, commit, trees, verified blobs
  GH-->>D: immutable sources or typed unavailability
  D->>P: bounded non-executing projection
  P-->>D: canonical facts, unknowns, and call edges
  D->>C: admit conservative observe-only source when unambiguous
  C-->>D: admitted draft or diagnostics
  D-->>API: exact report and reviewable or blocked manifest
  API-->>B: bounded discovery projection
```

No arrow in this flow reaches epoch registration, activation, workflow
dispatch, provider mutation, or omission authority. Owner:
[workflow discovery](modules/workflow-discovery.md).

#### 5.2.1 Dormant Target-Authority Evidence

```mermaid
flowchart LR
  B["Native Phase-0 baseline"] --> U["Derived expected relation"]
  D["Owner-approved transition"] --> U
  GH["Exact Git-object reads"] --> W["Stable workflow manifest"]
  GH --> S["Exact source binding"]
  A["Closed target artifacts"] --> R["Independent registration candidates"]
  W --> O["Independent observation candidates"]
  S --> O
  R --> RP["Registration projection"]
  O --> OP["Observation projection"]
  U --> RP
  RP --> C["Exact unactivated relation closure"]
  OP --> C
  B --> E["13-role canonical evidence bundle"]
  D --> E
  U --> E
  W --> E
  S --> E
  R --> E
  O --> E
  C --> E
  E --> V["Owner-codec replay"]
  V --> F["Atomic offline publication"]
  V -. "cannot imply" .-> X["Production omission authority"]
```

The observation key domain is derived without baseline, transition, expected,
or registration keys. Registration intentionally derives owner intent from the
expected relation and separately enumerated target declarations. The two
outputs close by canonical full-row bytes. The retained bundle replays that
closure and may be published only as an offline content-addressed artifact;
neither replay nor publication provides persistence, signer, runtime, or
production authority.

Owners: [workflow authority](modules/workflow-authority.md),
[target-authority producers](modules/target-authority-producers.md), and
[target-authority relation](modules/target-authority-relation.md). Retention
and offline publication are owned by
[target-authority evidence](modules/target-authority-evidence.md).

### 5.3 Governance Observation Dataflow

```mermaid
sequenceDiagram
  participant B as Browser
  participant API as Authenticated read route
  participant A as Fresh repository grant
  participant G as Governance observation
  participant GH as GitHub installation adapter

  B->>API: exact installation and repository scope
  API->>A: require Keycloak read role and exact App scope
  A-->>API: authorized actor and installation relation
  API->>G: actor and exact scope
  G->>G: authorize before provider I/O
  G->>GH: read repository identity
  GH->>GH: read bounded active default-branch rule pages
  GH->>GH: re-read and compare repository identity
  GH-->>G: complete canonical rules or typed failure
  G-->>API: best-effort unbaselined observation
  API-->>B: no-store bounded projection
```

No arrow persists a baseline, classifies compliance or drift, or reaches a
provider mutation. Owner:
[governance observation](modules/governance-observation.md).

### 5.4 Governance Baseline Approval Dataflow

```mermaid
sequenceDiagram
  participant B as Browser
  participant API as Baseline route
  participant A as Control-plane scope authorization
  participant S as Baseline service
  participant G as Governance observation
  participant DB as Capability-attested PostgreSQL

  B->>API: scope, operation, expected digest and active pointer
  API->>A: session, exact Origin, CSRF, configure role, App scope
  A-->>API: authorized actor and installation relation or denial
  alt forbidden
    API-->>B: forbidden#59; no baseline store or provider read
  else authorized
    API->>S: trusted actor and bounded command
    S->>DB: resolve exact operation replay
    alt exact operation retained
      DB-->>S: retained baseline
      S-->>API: duplicate or unchanged#59; no provider read
    else new operation
      S->>DB: read active predecessor
      S->>G: freshly re-observe exact repository scope
      G-->>S: complete canonical state or typed failure
      S->>S: compare requested digest and prepare single-use acceptance
      S->>DB: scope lock#59; recheck operation and predecessor#59; append exact result
      DB-->>S: accepted, unchanged, conflict, or unavailable
      S-->>API: typed bounded result
    end
    API-->>B: no-store retained state or typed failure
  end
```

The provider read and database transaction are not one snapshot. The command
therefore binds the exact observed state digest, while the transaction
independently revalidates the complete predecessor pointer. The resulting
artifact is expected state, not evidence of current compliance. Owner:
[governance baseline](modules/governance-baseline.md).

### 5.5 Proposal Review Registration Dataflow

A Keycloak session establishes control-plane identity. A separate one-use
GitHub step-up establishes repository-manager authority for one exact
proposal; neither identity substitutes for the other.

```mermaid
sequenceDiagram
  participant B as Browser
  participant API as Attestation routes
  participant K as Keycloak session and CSRF admission
  participant G as GitHub reviewer authorization
  participant D as Current-head proposal discovery
  participant DB as Capability-attested PostgreSQL

  B->>API: Start step-up for exact scope and proposal
  API->>K: Admit session, configure role, Origin and CSRF
  K-->>API: Keycloak principal and session identity
  API->>DB: Retain one-use proposal-bound transaction
  API-->>B: GitHub authorization redirect
  B->>G: Choose reviewer identity
  G-->>API: Exact callback with state and code
  API->>DB: Check pending transaction and session binding
  API->>G: Resolve immutable reviewer and maintain/admin evidence
  API->>D: Reproduce exact current-head proposal and baseline
  D-->>API: Admitted proposal or typed blocker
  alt all review premises hold
    API->>DB: Atomically consume transaction, register epoch and retain review/audit
    API-->>B: Retained review#59; activation is separate
  else missing or stale premise
    API-->>B: Typed failure#59; no accepted review
  end
```

The GitHub user token is discarded after its bounded operation; it does not
become a durable browser session. Provider observation and PostgreSQL commit
are not globally atomic. Later activation rechecks proposal identity, active
baseline and fresh reviewer permission. Owners:
[control-plane identity and repository attestation](modules/control-plane-identity-and-repository-attestation.md)
and [proposal review registration](modules/proposal-review-registration.md).

## 6. State and Transaction Ownership

The boxes below are logical ownership surfaces inside one PostgreSQL boundary,
not separate databases. A use case owns a transaction; repositories do not
commit independently.

```mermaid
flowchart TB
  UOW["Application-owned UnitOfWork"] --> DELIVERY["Webhook delivery idempotency"]
  UOW --> EPOCHS["Immutable config epochs and activation"]
  UOW --> BASELINES["Append-only governance baselines"]
  UOW --> REVIEWS["Immutable proposal reviews and non-activating registration"]
  UOW --> ISSUANCE["Issued plan replay and request identity"]
  UOW --> PRODAUTH["Immutable production authorities and scope bindings"]
  UOW --> RECONSTATE["Reconciliation state and CAS revision"]
  UOW --> SHADOW["Shadow candidate and observed result"]
  UOW --> OVERRIDES["Monotonic operator overrides"]
  UOW --> LEDGER["Append-only hash-linked audit events"]
  DELIVERY --> PG[("PostgreSQL 18.6")]
  EPOCHS --> PG
  BASELINES --> PG
  REVIEWS --> PG
  ISSUANCE --> PG
  PRODAUTH --> PG
  RECONSTATE --> PG
  SHADOW --> PG
  OVERRIDES --> PG
  LEDGER --> PG
  ATTEST["Revision, schema, data, and principal attestation"] --> PG
  ATTEST --> READY["Readiness"]
```

Owners: [persistence](modules/persistence.md),
[database compatibility](cross-cutting/database-compatibility.md),
[audit replay](modules/audit-replay.md), and
[operator controls](modules/operator-controls.md).

## 7. Process Lifecycle and Failure Containment

```mermaid
stateDiagram-v2
  [*] --> SettingsAdmission
  SettingsAdmission --> Rejected: invalid or incomplete settings
  SettingsAdmission --> Disabled: mode is disabled
  SettingsAdmission --> Compose: mode is non_enforcing
  SettingsAdmission --> ProductionAdmission: mode is enforcing
  ProductionAdmission --> Rejected: build, receipt, signature, time, or scope fails
  ProductionAdmission --> Compose: receipt admitted
  Compose --> Rejected: key, registration, inventory, or adapter closure fails
  Compose --> InitialReconciliation
  InitialReconciliation --> Rejected: timeout, failure, stop, or concurrent close
  InitialReconciliation --> Ready: one complete round succeeds
  Ready --> NotReady: dependency or reconciliation failure
  NotReady --> Ready: bounded recovery succeeds
  Ready --> Draining: shutdown requested
  NotReady --> Draining: shutdown requested
  Disabled --> Draining: shutdown requested
  Draining --> Closed: tasks drained and resources closed in owner order
  Draining --> CleanupPending: owner deadline, child unsettled
  CleanupPending --> DeadlineExceeded: child settles
  Draining --> DeadlineExceeded: owner deadline, child settled
  DeadlineExceeded --> [*]
  Rejected --> [*]
  Closed --> [*]
```

Disabled mode is live but intentionally not ready. Both connected modes become
ready only after dependency probes and initial reconciliation succeed;
enforcing composition additionally requires exact durable authority
registration before provider clients exist. Shutdown has one lifecycle owner
per resource and a bounded drain.

Owner: [runtime composition](modules/runtime-composition.md).

## 8. Deployment Boundary

This is a logical deployment projection, not an admission of a particular
platform, replica count, availability target, backup policy, or capacity. Those
facts remain external deployment-owner evidence.

```mermaid
flowchart LR
  GH["GitHub SaaS"]

  subgraph PLATFORM["Deployment-owned platform boundary"]
    INGRESS["TLS ingress and request limits"]
    SERVICE["Immutable CI Coordinator artifact<br/>replica count not yet admitted"]
    SECRETS["Secret and credential provider"]
    PG[("Externally migrated PostgreSQL 18.6")]
    OBS["Log and metric sinks"]
  end

  GH -->|"webhooks and plan requests"| INGRESS --> SERVICE
  SERVICE -->|"installation-authenticated API"| GH
  SECRETS -->|"runtime-only material"| SERVICE
  SERVICE -->|"transactions and readiness probes"| PG
  SERVICE -->|"redacted telemetry"| OBS
```

The service image owns process behavior. The deployment owns TLS termination,
network policy, secret delivery, database migration execution, artifact
identity, cardinality, and rollout. PostgreSQL concurrency contracts permit
cooperating processes, but that fact alone does not prove a horizontally scaled
deployment is operable or available.

Owners: [runtime settings](modules/runtime-settings.md),
[runtime composition](modules/runtime-composition.md),
[database compatibility](cross-cutting/database-compatibility.md), and the
[production admission contract](cross-cutting/production-admission.md).

## 9. Runtime Authority And External Admission

Python is the sole repository-owned backend runtime. Local implementation
completion is still not production authority: deployment requires external
evidence tied to one immutable artifact and target repository.

```mermaid
flowchart LR
  SOURCE["Python source and admitted product contracts"]
  LOCAL["Clean-revision local proof"]
  RELEASE["Exact-source release workflow"]
  ARTIFACT["Immutable Python service artifact"]
  EXTERNAL["External admission conjunction<br/>provider, deployment, shadow, rollback, stable gate"]
  ENFORCE["Bounded production enforcement"]

  SOURCE --> LOCAL --> RELEASE --> ARTIFACT --> EXTERNAL --> ENFORCE
  LOCAL -. "cannot substitute for provider evidence" .-> ENFORCE
```

Therefore:

```text
LocalProof and not ExternalAdmissionConjunction
  -> ProductionEnforcementForbidden
```

Owners:
[release artifact publication](cross-cutting/release-artifact-publication.md)
and [production admission](cross-cutting/production-admission.md).

## 10. Local Development Projection

Concurrent worktrees use one topology with independent identities rather than
forking the application architecture or reserving host ports in advance.

```mermaid
flowchart LR
  ROOT["Canonical worktree root"] --> ID["Collision-checked instance identity"]
  ID --> TASKS["Locked mise tasks"]
  TASKS --> CLI["Python lifecycle owner"]
  CLI --> SECRETS["Private file secrets outside worktree"]
  CLI --> PROJECT["Owned Compose project"]
  PROJECT --> PG["PostgreSQL 18.6"]
  PROJECT --> API["FastAPI"]
  PROJECT --> UI["Vite operator workbench"]
  UI -->|"server-side proxy"| API
  PROJECT --> PORTS["Docker-assigned loopback ports"]
```

Owner: [local development environment decision](../decisions/local-development-environment.md)
and [developer environment requirements](../specs/ci-coordinator-developer-environment/overview.md).

## 11. Diagram Coverage

The diagram set is complete for architectural orientation when a reader can
answer nine independent questions without inferring an unstated boundary.

| Question                                                                                               | Projection                        | Detailed owner                                  |
|--------------------------------------------------------------------------------------------------------|-----------------------------------|-------------------------------------------------|
| Who interacts with the system?                                                                         | System context                    | meta-specification                              |
| Where does trust change?                                                                               | Trust boundaries                  | security and identity specs                     |
| Which component owns each decision?                                                                    | Component topology                | context map and module specs                    |
| How does a plan move end to end?                                                                       | Dynamic plan dataflow             | dataflow and state machines                     |
| How are organization identity, repository consent, provider authority, and emergency safety separated? | Target organization control plane | organization control-plane contract             |
| Where is durable state committed?                                                                      | State and transaction ownership   | persistence specs                               |
| How is the artifact connected without claiming a deployment?                                           | Deployment boundary               | runtime, database, and external-admission specs |
| When may the runtime claim readiness or authority?                                                     | Lifecycle and migration gates     | runtime and production-admission specs          |
| How is a connected developer instance reproduced and isolated?                                         | Local development projection      | local environment decision and requirements     |

Detailed epoch, transaction, plan, database revision, shard, and replay state
machines remain in [Dataflow and State Machines](03-dataflow-and-state-machines.md).
Repeating them here would increase drift without adding a new orientation plane.
