# Application Use Cases Module Specification

Status: module specification

Date: 2026-07-09

## 1. Owned Invariant

Application use cases orchestrate transactions and ports while preserving domain
ownership: they sequence decisions, but do not redefine the predicates owned by
bounded contexts.

## 2. Public API

```text
request_dynamic_plan(command) -> PlanResponse
record_shadow_candidate(command) -> ShadowRecordResult
activate_config_epoch(command) -> ActivationResult
force_full_ci(command) -> OverrideResult
read_run_view(query) -> RunView
read_proof_view(query) -> ProofView
append_audit_event(command) -> AuditAppendResult
```

## 3. Private Boundary

The module owns cross-context use-case orchestration, its input and output port
protocols, transaction scoping, and cross-context command/query DTOs. A
single-capability input port remains with that capability; introducing an
application pass-through solely to satisfy a directory sequence is forbidden.
HTTP owns only transport-shaped ports. The module must not own HTTP schemas,
ORM models, GitHub SDK clients, or pure domain predicates.

## 4. Input Completeness Rules

Each command must carry:

```text
trusted identity or explicit unauthenticated marker
config epoch reference when planning depends on policy
repository/run subject reference
idempotency key when durable effects are possible
request hash for replayable decisions
```

Missing command facts must produce typed failures before partial durable effects.

`append_audit_event` owns the temporal boundary for raw audit input:

```text
raw command
  -> prepare PersistableAuditInputV1
  -> obtain UnitOfWork from an injected factory
  -> append the opaque prepared capability
  -> commit explicitly
```

Preparation failure occurs before the factory is invoked, so no connection or
transaction exists. The write UnitOfWork and prepared-only repository are
private capabilities, not public raw-input APIs. A composite use case prepares
every independently derivable input before requesting its UnitOfWork; facts that
must be selected from retained state remain transaction-owned and require a
separate bounded reconstruction proof rather than a caller-supplied callback.

## 5. Fallback Behavior

```text
MissingContext => call fallback-capable domain API or return typed FullCI response
DomainRejection => preserve rejection reason
StoreUnavailable => return typed unavailable result
PortUnavailable => return typed unavailable result
PartialEffectRisk => rollback unit of work
```

Use cases cannot change selected validation coverage except by invoking the
owning domain API.

## 6. Audit And Replay Facts

Every durable use case must append an audit event in the same unit of work as
the state transition. The replay facts are command hash, result hash, domain
decision references, config epoch, actor identity reference, and stable reason
code.

The unit-of-work result must distinguish `committed`, `duplicate`, and
`conflict`. `Duplicate` means the complete admitted effect pair already exists.
If exactly one effect exists, a retry repairs the missing effect in one commit
and reports `committed` only when retained facts uniquely reconstruct an
equivalent counterpart. A non-reconstructible proper subset returns `conflict`
without synthesizing state. Partial retained state must never be misclassified
as a complete duplicate. A conflict commits neither attempted effect.
The application passes only admitted subject state to a paired commit. It must
not supply an audit callback, because that callback could enqueue an independent
write that survives rollback. The store-owned pair constructor derives evidence
from the canonical retained record selected during state classification.

## 7. Forbidden Imports

```text
fastapi routers
sqlalchemy model classes
GitHub SDK clients
private domain modules
environment variables
```

## 8. Proof Obligations

| Obligation                                               | Falsifier                                                                                |
|----------------------------------------------------------|------------------------------------------------------------------------------------------|
| Use cases call public domain APIs only.                  | Use case imports planner private module.                                                 |
| Durable state and audit append are atomic.               | State changes without audit event.                                                       |
| Pair construction has no caller write capability.        | A caller audit callback enqueues evidence that persists after composite rollback.        |
| Retry classification describes the complete effect pair. | One retained effect is reported as a duplicate without repairing its counterpart.        |
| Port failures are typed.                                 | GitHub exception crosses use-case boundary.                                              |
| Domain predicates are not redefined.                     | Use case recomputes omission law directly.                                               |
| Byte rejection precedes transaction entry.               | Invalid raw audit input invokes the UnitOfWork factory or engine.                        |
| Raw input cannot bypass preparation.                     | A public write port accepts `AuditEventInput` instead of the opaque prepared capability. |

## 9. Implementation Mapping

As-built owner files:

```text
ci_coordinator/app/dynamic_plan.py
ci_coordinator/app/dynamic_plan_service.py
ci_coordinator/app/candidate_planning.py
ci_coordinator/app/capacity_planning.py
ci_coordinator/app/config_management.py
ci_coordinator/app/reconciliation_*.py
ci_coordinator/app/override_resolution.py
ci_coordinator/app/audit_persistence.py
```

## 10. Acceptance Tests

- use-case import-boundary tests.
- transaction rollback and audit atomicity tests.
- typed port failure tests.
- no private-domain-import tests.
- command completeness negative tests.
