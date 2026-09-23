# Backend Architecture Style

Status: accepted

Date: 2026-07-16

## 1. Decision

CI Coordinator is a contract-first modular monolith with:

- ports and adapters only at evidenced boundaries;
- strategic domain boundaries and selective tactical DDD;
- immutable values and a functional core inside an imperative application shell;
- strong consistency inside each authoritative PostgreSQL transaction;
- explicit durable convergence for multi-transaction workflows;
- bounded eventual consistency across provider boundaries;
- lightweight CQRS for operator workbench commands and projections in the same
  FastAPI application and PostgreSQL database.

It does not use an internal broker, Event Sourcing, a distributed command bus,
or a separate read database. Those mechanisms are not admitted by this
decision.

## 2. Selection Rule

For pattern `P`, scope `S`, evidenced forces `F`, benefit `B`, and total
complexity cost `C`:

```text
Applicable(P, S) :=
  Evidence(F, S)
  and ProtectsRequiredInvariant(P, F)
  and PreconditionsHold(P, S)
  and B(P, S) > C(P, S)

Selected(P, S) :=
  Applicable(P, S)
  and not exists Q: ParetoDominates(Q, P, S)
```

A pattern is therefore selected only when no simpler option is at least as
correct, operable, testable, and maintainable. Novelty and pattern popularity
are not evidence.

The current selection follows:

```text
OneDeployableBackend
and OneTransactionalDatabase
and StrongLocalInvariants
and VolatileExternalProviders
and PlannedOperatorReadModels
and not IndependentInternalConsumers
=> ModularMonolith
   + BoundedPortsAndAdapters
   + TransactionalWriteSide
   + SameStoreLightweightCQRS
   + ProviderReconciliation
```

## 3. Boundary Laws

[Application contract](backend-architecture-style/application-contracts.md#3-boundary-laws).

### 3.1 Module boundary

[Application contract](backend-architecture-style/application-contracts.md#31-module-boundary).

### 3.2 Port admission

[Application contract](backend-architecture-style/application-contracts.md#32-port-admission).

### 3.3 Tactical DDD

[Application contract](backend-architecture-style/application-contracts.md#33-tactical-ddd).

## 4. Consistency Model

[Application contract](backend-architecture-style/application-contracts.md#4-consistency-model).

## 5. Operator Workbench

[Application contract](backend-architecture-style/application-contracts.md#5-operator-workbench).

## 6. Asynchronous Work

[Application contract](backend-architecture-style/application-contracts.md#6-asynchronous-work).

## 7. Discovery Practices

[Application contract](backend-architecture-style/application-contracts.md#7-discovery-practices).

The companion preserves detailed rules in one place; this entrypoint retains
the decision, alternatives, consequences and revision conditions.

## 8. Consequences

Benefits:

- local invariants remain atomic and directly testable;
- provider failures remain explicit without infecting local state with
  unnecessary eventual consistency;
- the UI can evolve rich projections without coupling them to write aggregates;
- future distribution requires evidence and an explicit replacement decision.

Costs:

- module and port admission must be reviewed continuously;
- long-running work needs durable state and lease or compare-and-swap semantics;
- read DTO evolution requires its own compatibility discipline.

## 9. Decision Certificate

### 9.1 Candidate ledger

| Candidate                               | Disposition | Decisive evidence                                                                                                                                                 |
|-----------------------------------------|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Contract-first modular monolith         | selected    | One deployment and one transactional authority satisfy every current capability while package boundaries isolate distinct domain, trust, and provider semantics.  |
| Technical-layer monolith                | rejected    | Controllers/services/repositories as top-level owners would couple unrelated capabilities and obscure invariant ownership without reducing deployments or stores. |
| Microservices                           | rejected    | No independently owned, deployed, or scaled internal capability currently offsets extra network failure, versioning, observability, and consistency boundaries.   |
| Internal broker and event-driven core   | rejected    | No independent consumer or delivery SLO requires asynchronous transport inside the deployable.                                                                    |
| Event Sourcing                          | rejected    | Product correctness does not require reconstructing authoritative state from an immutable event stream.                                                           |
| Separate CQRS read store                | rejected    | No measured read pressure, schema conflict, or independent scaling requirement justifies projection lag and another operational authority.                        |
| Workflow-only or serverless coordinator | rejected    | Transactional configuration epochs, durable review/audit state, reconciliation, and an operator workbench require a cohesive durable service boundary.            |

The ledger is decision-sufficient for the materially distinct deployment,
persistence, and internal-organization strategies supported by current
evidence. It does not claim completeness over unknown future alternatives;
new evidence or a candidate with a distinct operational consequence reopens
the decision.

### 9.2 Objective and units

Selection is lexicographic rather than a fabricated weighted score:

1. satisfy every correctness, security, reliability, and ownership invariant
   (`pass/fail`);
2. minimize independently deployed runtime components (`count`);
3. minimize distributed consistency and provider failure boundaries (`count`);
4. minimize privileged authority surfaces (`count`);
5. minimize change surface for one capability (`modules and contracts touched`);
6. maximize independently executable invariant falsifiers (`covered invariant
   count`, subject to meaningful risk ownership).

Any candidate failing step 1 is inadmissible. The selected design is preferred
under these declared forces: no independent consumer, deployment requirement
or measured shared-store limitation currently justifies the distributed
alternatives. The ledger does not prove a universal Pareto optimum. A
technical-layer monolith adds no runtime component, but its capability
ownership and change locality must still satisfy the boundary laws.

### 9.3 Evidence horizon and review triggers

This decision is valid through the first production release and subsequent
operation while its assumptions remain true. Re-evaluation is mandatory when
measurements or ownership changes establish any of:

- one capability needs independent deployment, release cadence, or scaling;
- shared-store reads violate an admitted latency or isolation budget;
- an independent consumer requires durable event delivery;
- one deployment cannot meet availability or recovery objectives;
- package boundaries repeatedly fail to contain capability changes;
- provider reconciliation load exceeds the measured single-service capacity
  envelope.

The replacement decision must record the triggering observation, retain
failure semantics for remote calls, and prove migration and rollback. Pattern
preference alone is not a trigger.

## 10. Falsifiers

This decision is defective if any of the following occurs without a replacing
ADR:

1. a domain package imports transport or persistence infrastructure;
2. a port has no admitted boundary predicate;
3. a UI projection mutates authoritative state;
4. local state and its required audit record can commit independently;
5. missing or stale provider evidence is rendered as success;
6. a broker, event store, command bus, or read database is introduced without
   its admission evidence;
7. two modules assign different owners to the same invariant.
