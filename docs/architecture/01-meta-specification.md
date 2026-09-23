# CI Coordinator Meta-Specification

Status: system-law authority

Last verified: 2026-08-30

## 1. Purpose And Authority

This document owns CI Coordinator's product-level system laws. It does not own
module internals, provider behavior, or deployment readiness. Lower-level
contracts may refine these laws but may not weaken them.

For architecture epoch `E`, using premise validity from
[`00-system-axioms.md`](00-system-axioms.md):

```text
AdmissibleLaw(l, E) :=
  OwnerAdopted(l, E)
  and forall p in RequiredPremises(l): ValidPremise(p, E)

LawApplies(l, x, E) :=
  AdmissibleLaw(l, E)
  and Scope(l, x, E)
  and not RevisionTrigger(l, E)
```

A required premise is a necessary validity condition for its law. Selection,
feasibility, and provider-context premises constrain lower-level decisions but
do not become necessary law conditions merely because they motivated a design.
A premise does not entail a unique architecture. A lower-level decision is
admitted only after comparing a bounded, explicitly named alternative set
against the applicable laws and hard constraints.

## 2. System Laws

The requirement floor names the direct minimum product obligations constrained
by each law. Module requirements may add stricter, scope-specific obligations.
The versioned
[architecture traceability profile](../specs/ci-coordinator-core/architecture-traceability-profile.v1.json)
owns the complete owner-declared reference mapping, and
`requirements.admission` rejects missing, duplicate, unknown, dangling, or
out-of-repository routes. The local gate evaluates bounded current-worktree
inputs observed during one admission run; it does not claim an atomic
filesystem snapshot. Exact base/head identity and witness freshness belong to
the branch-head provider receipt. This mapping does not infer semantic
entailment from prose or path identity.

| Id    | Adopted law                                                                                                                                                  | Required premises | Direct requirement floor                                                 | Revision or falsifier                                                                                                                   |
|-------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------|--------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| MS-1  | Deterministic, replayable proof may reduce validation work.                                                                                                  | PA-4, CA-1, CA-2  | REQ-CI-CORE-001, REQ-CI-CORE-004                                         | An admitted omission lacks exact input identity, proof replay, or a native dangerous-counterexample witness.                            |
| MS-2  | Agent advice may only preserve or increase validation coverage.                                                                                              | PA-4, CA-2, CA-3  | REQ-CI-CORE-001                                                          | Advice can omit an obligation, weaken depth, reduce shards below a correctness floor, or acquire runtime authority.                     |
| MS-3  | Unknown, stale, invalid, unsupported, or incomplete planning evidence produces FullCI or an explicit typed failure.                                          | PA-4, CA-3        | REQ-CI-CORE-001, REQ-CI-CORE-004, REQ-CI-RUNTIME-002, REQ-CI-RUNTIME-007 | Such evidence reaches selected execution or an ambiguous success state.                                                                 |
| MS-4  | Provider-green state is not semantic-green state.                                                                                                            | CA-1, CA-2        | REQ-CI-CORE-009, REQ-CI-RUNTIME-003                                      | A skipped, neutral, or provider-only conclusion proves an obligation without executed or replayable omission evidence.                  |
| MS-5  | The required gate identity is stable across selected workload, shard count, retries, and supported pull-request epochs.                                      | PA-7, CA-3        | REQ-CI-RUNTIME-002, REQ-CI-RUNTIME-003, REQ-CI-RUNTIME-007               | Dynamic selection causes the required check to disappear, remain pending, collide across attempts, or report for the wrong epoch.       |
| MS-6  | Runner capacity may change scheduling and shard allocation, never validation coverage or semantic success.                                                   | PA-4              | REQ-CI-CORE-005                                                          | Busy or unavailable runners remove an obligation, lower required depth, or create success.                                              |
| MS-7  | A browser UI may display admitted evidence and request governed transitions; it cannot create semantic success or server authority.                          | PA-3, CA-1, CA-2  | REQ-CI-CORE-008, REQ-CI-UI-001                                           | Browser state, generated types, or an operator gesture bypasses server admission or creates success.                                    |
| MS-8  | Every durable correctness decision binds immutable subject, epoch, inputs, outcome, and predecessor evidence and remains replay-verifiable.                  | PA-5, CA-1, CA-2  | REQ-CI-CORE-007, REQ-CI-CORE-012, REQ-CI-CORE-013                        | A durable decision can be changed in place, replayed against different bytes, or accepted with a broken evidence chain.                 |
| MS-9  | Runtime policy authority changes only through immutable, concurrency-safe epochs with explicit activation and rollback semantics.                            | PA-5, CA-1        | REQ-CI-CORE-016                                                          | Mutable process state becomes policy authority or a concurrent update bypasses epoch admission.                                         |
| MS-10 | Product behavior is admissible only when an active requirement owns it and a requirement-bound native falsifier can reject its dangerous counterexample.     | CA-1, CA-2, CA-4  | REQ-CI-CORE-003, REQ-CI-CORE-009                                         | Implementation behavior, generated schema, prose, or structural checks become product truth without the requirement and native witness. |
| MS-11 | Published wire, signature, canonicalization, persistence, and replay domains are immutable or explicitly versioned.                                          | CA-1, CA-2        | REQ-CI-CORE-010, REQ-CI-CORE-012, REQ-CI-CORE-013                        | A released interpretation changes without a version boundary and migration or rejection contract.                                       |
| MS-12 | An adopted target repository remains execution owner, and selected execution consumes exactly one admitted, subject-bound plan or its conservative fallback. | PA-7, CA-3        | REQ-CI-RUNTIME-007, REQ-CI-RUNTIME-026, REQ-CI-RUNTIME-027               | The coordinator executes repository commands centrally, or target execution diverges from its admitted plan without entering fallback.  |

If any cited premise is false or unknown, its dependent law is unproven for the
affected epoch. An unproven omission or execution-authority law cannot retain
dynamic omission authority.

## 3. Optimization Semantics

Cost optimization is subordinate to correctness. Let `P` be a candidate plan
for exact planning key `K`:

```text
Feasible(P, K) :=
  RequiredValidationCovered(P, K)
  and StableRequiredGate(P, K)
  and MergeProtocolSupported(P, K)
  and FallbackLivenessSatisfied(P, K)
  and AuditReplayable(P, K)
  and LeastPrivilegeSatisfied(P, K)
  and OperatorPolicySatisfied(P, K)

CandidatePlans(K) is finite
AdmissiblePlans(K) := { P in CandidatePlans(K) | Feasible(P, K) }
```

Only plans in `AdmissiblePlans(K)` may be compared by cost. An active policy
may define a bounded preference relation over measured non-negative values such
as CPU seconds, billed time, wall latency, queue delay, and operator effort:

```text
AdmittedCostPolicy(CP, K) :=
  CP.epoch = K.policyEpoch
  and every metric has an owner, unit, non-negative bounded domain,
      observation rule, and missing-value rule
  and CP.preference is irreflexive and acyclic
  and for every finite non-empty S within AdmissiblePlans(K),
      CP.tieBreak(S) deterministically returns exactly one member of S

Prefer(P, Q, K) :=
  P in AdmissiblePlans(K)
  and Q in AdmissiblePlans(K)
  and AdmittedCostPolicy(ActiveCostPolicy(K.policyEpoch), K)
  and ActiveCostPolicy(K.policyEpoch).prefers(Cost(P), Cost(Q))

Chosen(P, K) :=
  AdmissiblePlans(K) is non-empty
  and AdmittedCostPolicy(ActiveCostPolicy(K.policyEpoch), K)
  and P in AdmissiblePlans(K)
  and not exists Q in AdmissiblePlans(K): Prefer(Q, P, K)
  and P = ActiveCostPolicy(K.policyEpoch).tieBreak(
    NonDominated(AdmissiblePlans(K))
  )

AdmissiblePlans(K) is empty
  or not AdmittedCostPolicy(ActiveCostPolicy(K.policyEpoch), K)
  => PlanningOutcome(K) = TypedFailure
```

The global architecture does not invent universal weights or combine
incommensurable units. Exact scalarization, bounds, tie-breaking, and missing
telemetry behavior belong to the scoped optimization contract. Finiteness and
acyclic preference ensure a non-empty non-dominated set whenever an admissible
plan exists; the total deterministic selector then produces exactly one plan.
Unknown cost cannot authorize omission; an incomplete preference relation
preserves a correct candidate or uses the conservative fallback.

## 4. Closed Planning Predicates

All proof predicates are closed over explicit coordinates. Let:

```text
K := (repository, event, baseSha, headSha, policyEpoch)
D := diff evidence
G := dependency graph evidence
C := validation obligation
P := active policy
V := omission verifier
```

Then:

```text
KnownDiff(D, K) :=
  D.repository = K.repository
  and D.baseSha = K.baseSha
  and D.headSha = K.headSha
  and not D.truncated
  and D exactly represents every path changed from K.baseSha to K.headSha

FreshGraph(G, K, D) :=
  G.repository = K.repository
  and (
    G.epoch = K.headSha
    or (
      G.epoch is a trusted ancestor of K.headSha
      and ChangedPaths(D) intersects GraphInvalidationSurface(G, K) = empty
      and GraphInvalidationRule(G, K) is admitted and replayable
    )
  )

KnownSurface(C, G, K) :=
  C is active at K.policyEpoch
  and G contains the complete responsibility surface required by C

Affects(D, G, C, K) :=
  Impact(D, G) intersects ResponsibilitySurface(C, K.policyEpoch)
  or D touches GlobalRiskPaths(C, K.policyEpoch)
  or EvidenceFor(C, D, G, K) is unknown, stale, invalid, unsupported, or incomplete

CanOmit(K, C, D, G, P, V) :=
  P.epoch = K.policyEpoch
  and KnownDiff(D, K)
  and FreshGraph(G, K, D)
  and KnownSurface(C, G, K)
  and not Affects(D, G, C, K)
  and P.allowsOmission(C)
  and V.accepts(K, C, D, G, P)
```

Every implementation may refine these predicates but may not omit a coordinate
or turn an unknown term into `true`.

## 5. Semantic Success

For exact execution attempt `A` and obligation `C`:

```text
SemanticGreen(A, C) :=
  ExecutedAndPassed(A, C)
  or OmittedWithReplayableProof(A, C)

ProviderGreen(A, C) -/-> SemanticGreen(A, C)
```

The evidence must bind the same repository, event, base, head, policy epoch,
plan identity, obligation, attempt, and reporting channel. Success for one
coordinate cannot cross-admit another.

## 6. Coverage Order

The validation contract owns the finite obligation and depth algebras. For two
plans over the same planning key:

```text
P >= Q :=
  forall obligation C:
    Coverage(P, C) >= Coverage(Q, C)

FullCI(K) :=
  the plan selecting every active obligation at its policy-defined full depth
```

Agent advice is admissible only when its verified result is at least the
deterministic plan:

```text
AdmitAdvice(K, deterministic, advised) :=
  SameClosedObligationAlgebra(K, deterministic, advised)
  and advised >= deterministic
```

Shard allocation is outside this order unless a validation requirement itself
defines a correctness floor. Scheduling cannot redefine coverage.

## 7. Exact And Monotonic Conformance

Let `SPEC(x)` be the active requirement projection for input `x` and `OBS(x)`
the observable implementation result:

```text
Conforms(x) :=
  SafetyPlane(x)  => OBS(x) >= SPEC(x)
  WirePlane(x)    => OBS(x) = SPEC(x)
  AuditPlane(x)   => Replay(OBS(x)) = OBS(x)
  FailurePlane(x) => UnknownOrInvalid(x) maps to FullCI or explicit typed failure
```

Exact equality is required for canonical bytes, hash inputs, signature payloads,
schema versions, identity binding, idempotency coordinates, and audit-chain
verification. Monotonic strengthening is permitted only for validation
coverage and only when it preserves resource and operator contracts.

## 8. Abstract Authority Boundaries

These are semantic boundaries, not a mandate for one class or package per row.
The context map owns the current physical projection.

| Boundary                 | Owns                                                                   | Cannot own                                                |
|--------------------------|------------------------------------------------------------------------|-----------------------------------------------------------|
| Transport                | authentication, request admission, transport projection                | planning or success semantics                             |
| Application coordination | cross-context sequencing and transaction orchestration                 | duplicated domain predicates                              |
| Domain capability        | pure invariants and typed decisions                                    | HTTP, persistence mapping, provider SDKs                  |
| Persistence              | durable uniqueness, compare-and-swap, migration, transaction mechanics | omission or success predicates                            |
| Provider adaptation      | remote protocol translation and typed unavailable states               | semantic success                                          |
| Presentation             | admitted evidence and governed command requests                        | server authority or success creation                      |
| Advisory analysis        | additional risk and validation suggestions                             | omission, downgrade, credentials, or fallback disablement |

Choose the minimum boundary strength that enforces the applicable invariant.
Source separation alone does not prove semantic, operational, or lifecycle
independence.

## 9. Rejected Countermodels

The following system shapes contradict an adopted law:

- agent advice can remove a deterministic obligation;
- runner scarcity can reduce required coverage;
- browser state or an operator action can create semantic success;
- mutable process state can replace an immutable active policy epoch;
- a required workflow can disappear through workflow-level filtering;
- provider-green state can substitute for executed or replayable proof;
- target execution can consume a plan for another subject or epoch; or
- generated schemas or incidental behavior can create product truth.

Concrete packages, classes, DTOs, repositories, and ports are not required by
this list. They require separate context-relative justification.

## 10. File And Module Validity

```text
ValidUnit(U, E) :=
  ApplicableArchitectureRulesHold(U, E)
  and DependencyDirectionPreserved(U, E)
  and TrustBoundariesPreserved(U, E)
  and not ProvedForbiddenCoownership(U, E)
```

Size, export count, coupling, complexity, and churn select review candidates;
they do not prove semantic co-ownership or mandate decomposition. Verdict,
epoch, waiver, completeness, and evidence rules are owned by
[`module-ownership-and-decomposition.md`](cross-cutting/module-ownership-and-decomposition.md)
and its machine profile.

## 11. Lower-Level Specification Gate

A lower-level specification is admissible only when it defines, or explicitly
and accurately references:

1. its premise and system-law dependencies;
2. one canonical semantic owner and owned invariants;
3. public observables and private implementation freedom;
4. input identity, completeness, and trust transitions;
5. state, concurrency, database, and resource semantics when applicable;
6. typed failure, timeout, retry, cancellation, and fallback behavior;
7. durable evidence, idempotency, audit, and replay coordinates;
8. dependency direction and forbidden authority paths;
9. considered alternatives and why no simpler complete option dominates;
10. dangerous counterexamples and native falsifiers;
11. requirement and implementation mappings;
12. explicit non-claims, residual risk, and revision triggers.

If a proposed module cannot name a distinct semantic owner or an enforceable
boundary, it must not become a separate production package. Conversely, a
single file or package must not retain proved forbidden co-ownership merely to
reduce file count.

## 12. Non-Claims And Revision

This document does not prove:

- global optimality of the current architecture;
- completeness of any provider observation;
- production readiness, deployed topology, or provider availability;
- that every requirement has a runtime implementation witness;
- that package boundaries alone prevent authority leakage; or
- that one cost preference is universally correct.

Re-evaluate the affected laws when a cited premise, provider fact, supported
event model, execution-ownership decision, compatibility policy, or proof
authority changes. A material law change requires a new architecture epoch,
updated requirement traceability, and fresh native witnesses.
