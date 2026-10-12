# Architecture Decision Quality

Status: governing cross-cutting review contract

Date: 2026-07-19

## 1. Decision Rule

Architecture is admissible only relative to an explicit context. For a decision
`d` in context `c`, define:

```text
Context(c) :=
  ObservableBehavior
  x HardRequirements
  x QualityBudgets
  x TrustBoundaries
  x ConsistencyNeeds
  x LifecycleAndVersioning
  x DeploymentTopology
  x OwnershipAndGovernance
  x Reversibility

Admissible(d, c) :=
  PreconditionsHold(d, c)
  and PreservesObservableBehavior(d, c)
  and SatisfiesHardRequirements(d, c)
  and FitsQualityBudgets(d, c)
  and BoundariesAreProven(d, c)
  and DependenciesFollowOwnedSemantics(d, c)
  and EvidenceCanFalsifyClaims(d, c)
  and EnforcementExistsForMechanicalRules(d, c)
  and AlternativesWereCompared(d, c)
  and RevisionConditionsAreExplicit(d, c)
```

`Preferred(d, c)` means that `d` is admissible and no compared admissible
alternative is strictly better on every declared objective. It does not mean
that `d` is globally optimal.

### Consequence

Assume a context-free architecture `a` is optimal for every context. Choose one
context that requires a single serializable transaction and another that
requires independent failure and deployment domains. The boundary strength
that minimizes coordination cost in the first context cannot simultaneously
minimize coupling and blast radius in the second. Therefore the assumption is
false: no architecture is optimal outside context.

### Determinism and least privilege

Deterministic authorization does not by itself prove least privilege. A
policy can deterministically deny every untrusted caller yet grant an
unnecessary write permission to a trusted read-only task. Review the exact
per-task necessary permission set and show that every granted capability is
required; repeatability alone cannot establish that inclusion. This
distinction also applies when interpreting historical design arguments.

## 2. Governing Principles

The following principles are mandatory review questions. A principle is not a
license to add structure; it limits structure to what the evidence earns.

| Principle                                                            | Required proof                                                                                                                                                                 | Smallest falsifier or revision trigger                                                                                                |
|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| Context precedes architecture                                        | Name the observable behavior, hard requirements, quality budgets, trust, lifecycle, topology, and ownership affected by the decision.                                          | A material constraint is absent or an alternative becomes preferable under an admitted constraint.                                    |
| Prove the boundary before choosing dependency direction              | Identify the semantic invariant, its owner, callers, and forbidden knowledge; then direct dependencies toward the owner of stable meaning.                                     | A dependency imports policy owned by its caller, or the claimed owner cannot decide the invariant independently.                      |
| Source independence is not semantic or operational independence      | Prove independent meaning, lifecycle, governance, failure behavior, and deployability separately.                                                                              | Two packages compile independently but require coordinated semantic or operational change.                                            |
| A detail is contextual                                               | Define `Detail(x, c)` only relative to observed behavior and quality budgets; framework, database, or provider code is not automatically a detail.                             | Correctness, security, or latency changes when `x` changes, despite its classification as irrelevant.                                 |
| An interface is a type boundary, not behavioral substitutability     | State preconditions, postconditions, failure semantics, temporal behavior, and resource budgets shared by implementations.                                                     | A type-compatible implementation changes retries, consistency, ordering, latency, or failure classification.                          |
| Top-level structure follows cohesive capabilities                    | Prefer bounded business capabilities and ownership when they minimize harmful dependencies; use technical grouping only where it is the more cohesive owner.                   | A capability change routinely crosses unrelated top-level packages, or a capability package becomes a mixed-responsibility container. |
| Use the minimally sufficient boundary strength                       | Choose the lowest-cost boundary that satisfies trust, failure, consistency, scaling, deployment, and ownership requirements.                                                   | A weaker boundary satisfies every hard requirement, or the chosen boundary fails an admitted requirement.                             |
| DTOs require a semantic reason                                       | Add a DTO only across a change in semantics, trust, version, lifecycle, representation, or bounded projection.                                                                 | A DTO is field-for-field duplication with identical meaning and governance and adds no enforced invariant.                            |
| Repository and use-case classes are optional                         | Introduce them only when they own meaningful policy, substitution, transaction coordination, or repeated orchestration.                                                        | The class delegates one call, has one implementation, and enforces no independent rule.                                               |
| Material database semantics are explicit                             | Expose transaction, isolation, locking, uniqueness, ordering, pagination, index, and failure assumptions whenever correctness, security, or performance depends on them.       | Behavior changes under an admitted PostgreSQL plan, concurrency schedule, or isolation outcome that the contract omits.               |
| A remote call is never equivalent to a local call                    | Model timeout, cancellation, partial failure, retry, idempotency, rate limit, authentication, serialization, and stale evidence.                                               | The implementation treats an unavailable or ambiguous remote result as local success or a definitive absence.                         |
| Shared domain models require semantic identity and shared governance | Prove equal meaning, invariants, lifecycle, versioning, and owner authority before sharing.                                                                                    | Either consumer needs a field or invariant whose meaning the other owner cannot accept.                                               |
| Tests follow risk                                                    | Derive witness type and depth from severity, probability, detectability, concurrency, trust, and blast radius.                                                                 | A high-impact claim has only a happy-path unit example, or low-risk repetition adds no new falsifier.                                 |
| Hard requirements outrank aesthetics                                 | A documented security, reliability, performance, or compliance requirement may justify complexity only with evidence and a budget.                                             | The requirement is assumed, unmeasured, obsolete, or satisfiable by a simpler design.                                                 |
| An unenforced mechanical rule is a wish                              | Map every mechanically decidable rule to a linter, type checker, schema, import check, migration attestation, test, or Proofkit admission.                                     | A prohibited state can be committed while all assigned gates pass.                                                                    |
| Under uncertainty, prefer simple reversible structure                | Minimize irreversible commitments until evidence justifies stronger coupling or specialization.                                                                                | The simple choice violates a hard requirement, or measured recurring cost exceeds its revision threshold.                             |
| Remove unearned abstractions                                         | Retain an abstraction only if it removes demonstrated duplication or complexity, enforces an invariant, isolates a real volatility boundary, or enables required substitution. | Deleting or inlining it preserves behavior, boundaries, and quality budgets while reducing maintenance cost.                          |
| Every material decision is falsifiable and revisable                 | Record assumptions, alternatives, evidence, falsifiers, and revision conditions.                                                                                               | A decision cannot state what observation would invalidate it.                                                                         |

## 3. Boundary Selection

Boundary candidates are compared as a partially ordered set, not as a universal
ladder:

```text
B := {function, module, package, transaction, process, service, deployment}

Sufficient(b, c) :=
  TrustIsolation(b, c)
  and FailureIsolation(b, c)
  and ConsistencySupport(b, c)
  and ScaleSupport(b, c)
  and OwnershipSupport(b, c)

Choose b where Sufficient(b, c)
and no lower-cost admitted candidate is sufficient.
```

Cost includes runtime coordination, serialization, latency, deployment,
observability, testing, migration, and cognitive load. A service boundary is
therefore stronger in some dimensions and weaker in transactional simplicity;
it is never selected merely because it appears more decoupled.

## 4. Abstraction Admission

For a proposed abstraction `a`:

```text
Earned(a) :=
  EnforcesInvariant(a)
  or RemovesDemonstratedDuplication(a)
  or ReducesMeasuredComplexity(a)
  or IsolatesProvenVolatility(a)
  or EnablesRequiredSubstitution(a)

Keep(a) := Earned(a) and NetCost(a) < AvoidedCost(a)
```

An interface with one implementation, a repository that only forwards a call,
or a field-identical DTO is not automatically invalid. It is invalid when no
term of `Earned(a)` is demonstrated and removal preserves the admitted
contract.

## 5. Decision Record Contract

Every material architecture decision or module design must expose, directly or
by an authoritative reference:

1. context and observable behavior;
2. hard requirements and quality budgets;
3. assumptions and preconditions;
4. alternatives, including the simplest viable option;
5. selected boundary and dependency direction;
6. trust, consistency, failure, lifecycle, and database semantics;
7. accepted costs and explicit non-claims;
8. smallest falsifiers and assigned witnesses;
9. enforcement owner; and
10. revision conditions.

A missing item is admissible only when it is formally inapplicable and the
reason is stated. Repeating the same fact in multiple documents is prohibited;
references must preserve the authority map in `docs/architecture/INDEX.md`.

## 6. Enforcement Map

| Claim class                                   | Minimum enforcement                                                                            |
|-----------------------------------------------|------------------------------------------------------------------------------------------------|
| Python import direction and package ownership | import-boundary policy plus type checking                                                      |
| Public data shape and bounded parsing         | typed models, exact schema admission, and negative tests                                       |
| Database correctness and authority            | migration replay, schema and ACL attestation, transaction-level integration tests              |
| Remote-provider semantics                     | bounded transport adapters, typed failure algebra, retry/idempotency tests                     |
| Security boundary                             | startup admission, cryptographic tests, origin/CSRF/authentication falsifiers, secret scanning |
| UI/backend compatibility                      | deterministic OpenAPI projection, generated types, runtime response admission, browser tests   |
| Requirement traceability                      | Proofkit requirement-source admission, exact binding tuples, native witnesses                  |
| Performance and reliability budgets           | measured benchmark, load, fault, or concurrency witness when the budget is material            |
| Human semantic ownership                      | named review owner and falsifiable decision record; automation cannot infer product meaning    |

Static enforcement is preferred only for mechanically decidable predicates.
Semantic ownership, product meaning, and whether an abstraction is earned still
require evidence-based review; pretending these are statically decidable would
create false assurance.

## 7. CI Coordinator Application

This repository applies the contract as follows:

- top-level backend packages own cohesive CI capabilities rather than generic
  controller/service/repository layers;
- application functions are introduced for orchestration, while use-case
  classes and repositories remain optional;
- ports isolate real provider, persistence, clock, and cryptographic behavior,
  not every internal call;
- PostgreSQL locking, isolation, ACL, and migration semantics are explicit where
  they determine correctness;
- GitHub interactions use remote-call failure and freshness semantics rather
  than local-call assumptions;
- frontend DTOs are bounded projections across a trust and version boundary;
- Proofkit binds claims to witnesses but does not own business meaning; and
- uncertain future capabilities remain simple and reversible until product or
  operational evidence earns a stronger boundary.

Passing this contract means that the current evidence has not falsified the
decision. It does not prove context-free optimality, future optimality,
deployment readiness, or production behavior.

## 8. D9-C1 Comparison Ledger (2026-10-11)

Status: bounded CI-065 comparison ledger; no behavioral delta adopted.
It applies sections 1 to 6 to the D9-C1 candidates and keeps its dated
evidence here instead of in a separate document.

### 8.1 Scope And Freeze

[ROADMAP D9-C1](../../../ROADMAP.md#d9-c1-contract-and-testing-architecture-comparison)
owns the acceptance predicate; CI-056 shares it for abstractions. This ledger
compares candidate invariants with the current local owners. It is not a
waiver, a defect register or proof of global optimality.

- Source: local stacked head `a1966f616e370b8f4cd64190f26afb97d649c940`.
  It is unmerged; rebind every row to the merged tree before closure.
- External candidate: none. Provisional owner decision (2026-10-11): no
  external ADR or proposal is admitted, so the candidates are the local
  architecture, contract and browser-proof owners named in D9-C1.
- Evidence: owner documents and source read statically. No native,
  database, browser or provider witness was executed for this ledger.

Dispositions use the D9-C1 vocabulary: `already covered`, `adopt`,
`bounded pilot`, `defer`, `not applicable` or `unresolved`. `already covered`
means the named owner and enforcement decide the invariant on current source;
native qualification still belongs to that owner's route.

### 8.2 Boundary Contracts

| ID | Candidate invariant | Protected behavior | Owner | Enforcement | Counterexample or question | Cheaper alternative | Cost | Disposition and trigger |
|----|---------------------|--------------------|-------|-------------|----------------------------|---------------------|------|-------------------------|
| B-01 | One directional wire policy for untrusted requests and trusted responses | Exact keys, no undeclared coercion, finite JSON | [HTTP model contracts](../../features/http-model-contracts.md) | Pydantic base models, negative request tests, three-operation API exploration | No current counterexample; generated cases cover three operations only (CI-057 API03) | Keep the base models; no contract registry or extra layer | None now; API03 widening is per operation | Already covered. Reopen on a new wire policy or an operation outside the exploration corpus. |
| B-02 | Generated OpenAPI and TypeScript are projections, never authored authority | Consumer schema parity with the runtime graph | HTTP model contracts; operator UI requirements | Stale-contract rejection in `contract:check` (CI-057 API01 covered) | None found | Existing generator and check | None | Already covered. Reopen if a hand-edited projection gains authority. |
| B-03 | Browser runtime schemas restore exact bounds before semantic use | UI never interprets an unadmitted payload | HTTP model contracts, observable 5 | Per-API `schema.ts` admission with unit tests | None found | Existing per-API schemas; no shared runtime-validation layer | None | Already covered. Reopen on a payload consumed without its schema. |
| B-04 | Canonical JSON key order is identical across runtimes, including non-BMP keys | Digest and identity equality between backend and browser | [Kernel](../modules/kernel.md) canonical JSON; governance observation `canonicalJson.ts` | Both runtimes order keys by code point. The frontend schema test fixes `\ue000` before `\u{10000}`. On the frozen source the backend vector (`a`, `z`, U+00E4, U+1F600) orders identically under UTF-16, so it could not detect a UTF-16 sort; this commit adds U+E000 to `test_orders_keys_by_unicode_codepoint` so it can | A backend UTF-16 sort places U+1F600 before U+E000 and fails the extended vector; the frontend pair fails it in the browser runtime. Neither test was executed for this ledger | A shared cross-runtime vector file | Small, but adds no falsifier beyond the two runtime vectors | Already covered once the extended backend vector passes its native route. Reopen when a third runtime computes canonical identity. |
| B-05 | Durable payload families have closed versioned readers | Cross-generation replay and hybrid payloads fail closed | [Database compatibility](database-compatibility.md); per-feature payload owners | Capability attestation and closed reader branches, for example [explicit review renewal](../../features/explicit-review-renewal.md) | None found on the frozen source | Existing per-family readers; no generic version adapter | None | Already covered. Reopen on a durable family without a closed reader. |
| B-06 | A capability identifier keeps one descriptor across revisions; a changed descriptor needs a new versioned identifier | An identifier never names two descriptors | [Migration protocol](../modules/database-migration-protocol.md) | Predicate `I`: write-once identifier-to-descriptor admission over the retained lineage; a retired identifier may return only with its original descriptor hash | Native execution on the post-stack head is pending | Existing lineage check | None | Already covered. Reopen if a retired identifier can return with another descriptor. |
| B-07 | Audit records verify before filtered presentation | No unverified hidden event shapes a view | [Audit replay](../modules/audit-replay.md); CI-057 CI15 | Bounded-order verification witness | Full property partition remains open in CI-057 CI15 | Existing chain verifier | Medium, tracked by CI-057 | Already covered at owner level. Reopen with a filtered read that bypasses verification. |
| B-08 | Authority epochs fence stale writers: configuration epochs, production generations, logout generations | Old revisions or sessions never regain authority | [Generation-fenced production authority](../../features/generation-fenced-production-authority.md); [configuration epochs](../modules/config-epoch-lifecycle.md); [control-plane identity](../modules/control-plane-identity-and-repository-attestation.md) | Owner-specific generation checks and PostgreSQL concurrency tests | Is a shared epoch abstraction cheaper? Each owner has different lifetime, storage and recovery semantics | Keep owner-specific fences | A shared abstraction costs a cross-owner migration | Defer a generic epoch registry. Reopen when two owners need identical epoch semantics. |
| B-09 | Provider effects are fenced when their outcome can be unknown | No duplicate or lost repository mutation | [Authority transition safety](authority-transition-safety.md) section 3 | Activation predicate stays false | Static read finds only two POST calls: installation-token and OAuth token exchanges, which change no repository | None needed | None | Not applicable. Reopen before the first repository-changing provider operation. |

### 8.3 Transfer Safeguards

The three predicates of the
[authority transition contract](authority-transition-safety.md)
were re-read against the frozen source. Each is evaluated independently.

| ID | Mechanism | Current evidence | Counterexample or question | Cheaper alternative | Cost | Disposition and trigger |
|----|-----------|------------------|----------------------------|---------------------|------|-------------------------|
| T-01 | Per-copy durable conservation | Section 1 predicate: authority moves between representations, or a removal claim covers WAL, backup, quarantine or keys. History retention states that primary-row removal does not prove erasure from those copies. | Contract migrations retire capability readers, for example `20260915_0014`, and `20260926_0016` also deletes pending review challenges under the compatibility fence. They match the section 2.1 trigger "a destructive migration retires a reader or representation", but not the narrower section 1 predicate: no authority moves and no copy claim exists. | Clarify the section 2.1 trigger in its owner; implement no copy tracking | Documentation only | Unresolved. The owner decides which trigger governs. Reopen at the first CI-067 restore or erasure claim covering copies. |
| T-02 | Ambiguous remote-effect lifecycle | No target-changing provider operation exists (B-09) | None | None needed | None | Not applicable. Reopen before the first repository-changing operation. |
| T-03 | Independent full-row target-authority relation | Implemented under the accepted [plan](../../features/target-authority-relation-implementation-plan.md): `close_target_authority_relation` in `target_authority_relation/comparison.py` compares the derived relation with both inventories row by row, and `admit_staged_production_evidence` in `production_admission/relation_admission.py` rejects staged evidence that differs from the signed relation. Witnesses: `test_agreed_omission_by_both_producers_still_conflicts_with_expected_relation` and `test_staging_rejects_every_independently_resigned_relation_mismatch` | Native qualification on the post-stack head is pending; production omission authority remains unissued | None needed | None | Already covered at source and admission level. CI-047 owns native qualification and production activation. Reopen if any omission authority is admitted without this relation. |

### 8.4 Testing Guidance

| ID | Candidate guidance | Current witness and owner | Counterexample or question | Cheaper alternative | Cost | Disposition and trigger |
|----|--------------------|---------------------------|----------------------------|---------------------|------|-------------------------|
| G-01 | Block focused browser tests and reuse the exact built assets | [Browser proof admission](../../features/browser-proof-admission.md) | Native qualification pending | Existing Playwright and Biome options | None | Already covered. Reopen when the browser configuration changes. |
| G-02 | Oracles must identify the claimed guard, not a generic failure | [Causal proof oracles](../../features/causal-proof-oracles.md); governed mutation suites | Coverage is the named boundaries, not every test | Extend per boundary | Per oracle | Already covered for admitted boundaries. Reopen on a witness whose failure does not identify its guard. |
| G-03 | Automated accessibility with axe | Operator UI browser tests (CI-057 FE04) | Full WCAG needs assistive-technology review | Keep axe as the automated subset | Large for full WCAG | Already covered for the automated subset; full WCAG stays unresolved in CI-057 FE04. |
| G-04 | Visual regression snapshots | No screenshot comparison exists; layout is checked by semantic browser assertions | No owner requirement names a visual-consistency predicate | Semantic assertions | New baseline images and platform pinning | Defer. Reopen when an owner admits a visual requirement. |
| G-05 | Fixture ownership, isolation and exact-artifact binding | [Testing and Proofkit](testing-and-proofkit.md); manifest-verified asset reuse | None found | Existing fixtures and manifests | None | Already covered. Reopen on a shared mutable fixture across witnesses. |
| G-06 | Flake diagnosis and CI cost | CI-006 R-2 records container smoke build timings without changing its 600-second bound | [R-2](../../features/arm-ci-runner-migration.md#open-build-cost-observation-r-2) observed 10.5-11 minute builds and timeouts above the bound; its cause is `unknown` and no same-profile record set exists yet | Measure before changing any bound or build | Native run time | Defer to CI-006. Decide only on same-profile success and timeout records. |
| G-07 | Agents may propose tests or advice but never approve correctness | [Agent risk advice](../modules/agent-risk-advice.md) carries no execution authority (CI-057 CI05, AG05) | None found | Existing advice admission | None | Already covered. Reopen if advice or generated tests gain gate authority. |

### 8.5 Result

No candidate is adopted, so no behavioral delta, migration, plan or
review-decision record follows. The only source change is the B-04 backend
witness vector, which gains the falsifier its frontend counterpart already
had. One question is unresolved: T-01, whose
section 2.1 trigger is broader than its section 1 predicate. Its owner must
decide; this ledger does not change that contract. B-07 and G-03 retain
open partitions in CI-057.

The comparison is complete for these 19 local candidates only. It is not a
claim that no other candidate exists. It neither proves that the current
architecture is optimal nor qualifies any row natively. Rebind after merge,
an admitted external candidate, or a changed owner.
