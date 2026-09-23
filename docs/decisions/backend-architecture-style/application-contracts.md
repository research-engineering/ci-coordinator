# Backend Architecture Application Contracts

Status: current decision companion

The [backend architecture decision](../backend-architecture-style.md) owns
the selected architecture, alternatives and review triggers. This companion
owns its detailed application rules for module boundaries, consistency and
admission of additional mechanisms. Change these rules only with the
corresponding decision or capability contract.

## 3. Boundary Laws

### 3.1 Module boundary

A package boundary is justified when it owns at least one of:

1. a distinct domain language and invariant set;
2. a trust or admission boundary;
3. a transaction or lifecycle boundary;
4. a provider anti-corruption boundary;
5. an independently testable pure decision capability.

Packages may not be split solely to mirror framework layers or individual
tables. Domain packages never import HTTP, SQLAlchemy, or provider adapters.

### 3.2 Port admission

A `Protocol` is admitted only when at least one predicate is true:

```text
ExternalTechnicalBoundary
or TrustBoundary
or TransactionBoundary
or LifecycleBoundary
or SubstitutableProvider
or DistinctErrorAlgebra
or IndependentApplicationCapability
```

Structural typing used only to make a single internal call mockable is not a
port. Tests should use the concrete pure function or object in that case. Two
identical provider contracts with the same owner are one port, not two local
aliases. Port count is a review signal, never proof by itself.

### 3.3 Tactical DDD

- Value objects are immutable and valid by construction.
- Entities exist only for stable identities with lifecycles.
- Aggregates match real consistency boundaries, not directory boundaries.
- Repositories expose aggregate persistence or purposeful queries, not tables.
- A Unit of Work owns the local state-and-audit transaction.
- Factories exist only when construction has non-trivial admission rules.
- Domain events require a named consumer and delivery contract.

Pure deterministic functions remain preferred for planning, verification, and
optimization. Mutable object graphs are not the default domain model.

## 4. Consistency Model

| Operation                                 | Authority                                 | Consistency                                   | Recovery                                                                                           |
|-------------------------------------------|-------------------------------------------|-----------------------------------------------|----------------------------------------------------------------------------------------------------|
| Config registration, activation, rollback | PostgreSQL epoch state                    | one local transaction with compare-and-swap   | retry only from a fresh revision                                                                   |
| Plan issuance and audit                   | issuance Unit of Work                     | one local transaction                         | idempotent replay by request identity                                                              |
| Operator override and audit               | override Unit of Work                     | one local transaction and monotonic policy    | expiry or explicit rollback                                                                        |
| Reconciliation state                      | revision-bound PostgreSQL state           | one local transaction per admitted transition | periodic recovery; subject deadline, backoff, and terminal timeout are required before enforcement |
| GitHub workflow and check state           | GitHub                                    | bounded eventual consistency                  | typed observation, retry, reconciliation                                                           |
| Workflow discovery snapshot               | exact provider commit and blob identities | immutable snapshot                            | rescan on drift or staleness                                                                       |

There is no atomic transaction spanning PostgreSQL and GitHub:

```text
NoAtomicCommit(PostgreSQL, GitHub)
=> PartialFailurePossible
=> GlobalImmediateConsistencyImpossible
=> ImmutableIdentity + Idempotency + Reconciliation
```

This implication does not justify eventual consistency inside local
authoritative state.

Some derived local workflows intentionally span transactions. Reconciliation,
shadow comparison, and future discovery jobs therefore require durable states,
monotonic revisions, idempotent transitions, and an explicit convergence
contract. This is not a weakening of atomicity within any individual command.
The current runtime implements subject deadlines, bounded attempts and backoff,
database claim generations, expiring leases, and stale-owner fencing. Local and
PostgreSQL witnesses prove those mechanisms; they do not prove a deployed
multi-replica topology, provider availability, or production SLO satisfaction.
Enforcement therefore remains gated by the separate production-admission
contract rather than by missing convergence primitives.

## 5. Operator Workbench

The workbench uses lightweight CQRS, scoped to UI-facing capabilities:

```text
CommandHandler -> ExistingApplicationUseCase -> UnitOfWork -> PostgreSQL
QueryService -> AuthorizedReadRepository -> ProjectionDTO
```

Commands and queries have separate contracts because their responsibilities
differ, not because they require separate deployments. They share the same
FastAPI process and PostgreSQL database.

Read contracts must include:

- authenticated principal and repository scope;
- redaction policy;
- stable ordering and bounded pagination;
- `asOf` revision or immutable snapshot token;
- explicit freshness and unknown state;
- read-your-writes semantics after operator commands where required;
- no domain mutation through a projection repository.

The UI may submit authenticated commands for scans, proposals, epoch lifecycle,
and monotonic controls. It cannot derive success, bypass an application use
case, write projection tables directly, or turn missing evidence into green
state.

Repository policy and monotonic operator controls are hot-reloadable through
immutable database revisions. Process wiring, database endpoints, signing
material, and provider credentials are deployment settings and require process
replacement. Calling both classes "configuration" does not give them the same
lifecycle.

## 6. Asynchronous Work

Provider observations and workflow discovery may run as durable state machines
with idempotent jobs recovered from PostgreSQL. An in-process scheduler is
sufficient while one deployment owner and one recovery store satisfy measured
load and availability requirements.

Before more than one service replica may run a scheduler, the runtime must
either declare and enforce a single-active-replica topology or claim work with
database leases or compare-and-swap ownership. Concurrent replicas polling the
same unclaimed subject are not an admitted production topology.

An internal broker or transactional outbox is admitted only if a named external
or independently deployed consumer exists. A separate read store is admitted
only if measurements prove that the shared store cannot satisfy read latency,
write isolation, or independent scaling requirements.

| Candidate mechanism     | Admission evidence                                                                                          |
|-------------------------|-------------------------------------------------------------------------------------------------------------|
| Broker and outbox       | independent consumer or independently scaled worker plus delivery, ordering, idempotency, and recovery SLOs |
| Separate read database  | measured read pressure or schema conflict plus an admitted projection-lag contract                          |
| Event Sourcing          | operational state must be reconstructed from events and event evolution is explicitly governed              |
| Distributed command bus | independently deployed command owners and a defined partial-failure protocol                                |

Absent this evidence, each mechanism has positive complexity cost and no proven
correctness or operability benefit, so the selection rule rejects it.

## 7. Discovery Practices

BDD is used for collaborative requirement discovery, concrete acceptance
examples, and executable falsifiers. Gherkin is optional and is admitted only
when non-developer stakeholders maintain the scenarios.

Event Storming is a targeted discovery technique, not runtime architecture. It
is appropriate for unresolved cross-boundary sequences such as provider signal
identity, workflow discovery, and enforcement rollout. Its durable outputs are
updated terminology, invariants, state machines, requirements, and tests.
