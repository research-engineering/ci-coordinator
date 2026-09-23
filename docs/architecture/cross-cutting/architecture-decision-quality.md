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
