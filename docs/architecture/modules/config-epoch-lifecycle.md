# Config Epoch Lifecycle Module Specification

Status: module specification

Last updated: 2026-09-04

## 1. Owned Invariant

For one repository scope, an admitted policy epoch is immutable and an active
epoch changes only through one revision-safe, idempotent, audited transaction.
No successful transition leaves only an audit record, only a transition record,
or only a pointer mutation.

The ownership boundary is intentionally vertical. Registration and activation
share the same epoch identity and database capability; separating their runtime
authority would create an unusable intermediate state without reducing a real
dependency.

Policy parsing remains a pure `config_control` operation, but connected runtime
composition executes registration admission in one no-queue cancellable worker
process per backend process. A process-lifetime non-blocking gate is shared by
every adapter instance, while a private single-token worker limiter prevents
the ambient AnyIO process queue from owning progress. A busy, broken, or
unspawnable worker yields typed unavailability and cannot reach authorization
or persistence. Request cancellation terminates the running worker through the
admitted AnyIO process contract. This operational adapter changes neither the
valid policy domain nor the admission result algebra.

## 2. State And Algebra

Let `S = (installationId, repositoryId)` and let `E(S)` be immutable epochs
whose content-derived `epochId` is owned by `config_control`.

```text
EpochRegistry[S, epochId] = exact ValidatedEpochDraft bytes | absent
Registration[S, operationId] = immutable receipt and audit identity | absent
Active[S] = (epochId, revision) | absent
Activation[S, operationId] = immutable transition and audit identity | absent
1 <= utf8Bytes(ValidatedEpochDraft.contractResourceId) <= 4096
```

The content primitive `register(draft)` has exactly this result algebra:

```text
Created(epochId) | ExactDuplicate(epochId) | IdentityConflict(epochId)
```

Its conflict is content-identity conflict, not command conflict. A standalone
`registerOperation(command)` has this operation algebra:

```text
Committed(receipt) | ExactReplay(receipt) | OperationConflict(receipt)
```

The same content may be registered by distinct standalone operation
identifiers without creating a second epoch. The first operation atomically
inserts or reuses the content epoch, appends one pair-owned audit event, and
inserts one immutable registration receipt. An exact retry writes nothing.
Reusing the same scoped operation identifier with changed source, format,
actor, or admitted identity is a conflict. Proposal-review acceptance may use
the content primitive under its own operation receipt and paired audit event;
it does not fabricate a standalone registration receipt.

`activate(preparedCommand)` has exactly this result algebra:

```text
Activated(active) | ExactDuplicate(activation) | RevisionConflict(active)
| TargetUnavailable | OperationConflict
```

`rollback(preparedCommand)` uses the same revision and transaction algebra, but
adds one mandatory policy relation:

```text
RollbackAllowed(active, target) iff
  TargetRetained(target)
  and CoverageRelation(target, active) in {equal, greater}
```

`less`, `incomparable`, unknown, or unavailable comparison is fail-closed and
cannot mutate the active pointer.

An initial activation requires `expectedRevision = null` and creates revision
one. A later activation requires the exact current revision and creates its
strict successor. An operation replay is a duplicate only when every
client-supplied command fact and its retained audit identity are equal. The
repository reuses the retained server occurrence time while comparing a retry
after an unknown commit; a later clock reading cannot turn the same client
operation into a conflict. Reusing an operation identifier with different
client facts is a conflict. After scope authorization, the application resolves
those client facts before loading the live active pointer or recomputing rollback
coverage. Therefore an intervening activation cannot reinterpret a completed
rollback replay as a new live-state decision.

New command preparation projects an aware application-clock instant to UTC
before its existing millisecond serialization. Equivalent fixed-offset instants
therefore produce identical timestamps and audit inputs. A missing timezone or
undefined UTC offset remains invalid; it is never interpreted in the host's
local timezone. The existing command and audit validators retain their error
precedence before any store write. Exact activation and rollback operation replay
does not read the clock.

## 3. Safety Laws

```text
ImmutableEpoch:
  register(D) = Created => EpochRegistry[S, D.epochId] = D forever

PersistableDraft:
  admitted(D) => every contract resource identity in D is a non-empty
  Unicode-scalar string within the durable 4096-byte UTF-8 bound

ExactReplay:
  same epochId is a duplicate iff every stored durable field equals D

CAS:
  activate(S, r, E) = Activated(_, r + 1)
  iff Active[S].revision = r and E belongs to S

ABAResistance:
  A@r -> B@(r+1) -> A@(r+2)
  implies a command prepared against r cannot activate after B

AtomicPair:
  committed registration iff epoch insert-or-reuse, immutable receipt, and its
  pair-owned audit event are all committed in one transaction
  committed activation iff pointer, immutable transition, and its pair-owned
  audit event are all committed in one transaction
```

Proof of the last law: the repository acquires the scope lock before the global
audit-head lock, appends the pair-owned event, updates the pointer, and inserts
the immutable transition in one database transaction. Any typed conflict or SQL
failure marks that transaction rollback-required. PostgreSQL transaction
atomicity then makes partial visibility impossible to admitted readers.

## 4. Trust And Boundary Rules

- `config_control` owns pure admission and recomputation of every epoch hash;
  persistence re-admits supplied drafts before durable registration.
- `config_epochs` owns rollback command shape, the coverage-comparison port,
  result algebra, fail-closed admission, audit-input construction, and pointer
  transition semantics. It does not parse policy source or interpret planning
  coverage.
- `planning_core` implements the pure conservative coverage comparator because
  validation depth, witnesses, omission, and execution profiles are planning
  semantics. The dependency points from that provider to the consumer-owned
  `config_epochs` port; `config_epochs` cannot import `planning_core`.
- The PostgreSQL adapter owns storage codecs, bounded reads, row locks, and
  rollback marking. It does not decide policy validity or actor authorization.
- Generic audit append rejects the pair-owned activation event type. Only the
  lifecycle adapter can create it, so an audit-only activation cannot be
  constructed through the public ledger port.
- The caller supplies an already authenticated actor identity. The application
  authorizer proves repository scope before this module binds that identity into
  audit evidence; transport authentication remains owned by `api/http`.

## 5. Lock Order And Reconciliation

```text
compatibility fence
  -> scope advisory transaction lock
  -> active-pointer row lock
  -> audit-ledger-head row lock
```

The advisory lock serializes first activation, where no pointer row exists.
The immutable operation row is read before a new audit event is consumed. Thus a
retry after an unknown commit observes either the complete retained transition
as `ExactDuplicate` or no effect; it never creates a second transition.

## 6. Required Falsifiers

- forged but shape-valid epoch draft is rejected before registration;
- an empty, non-scalar, or over-4096-byte contract resource identity cannot
  construct an admitted epoch draft;
- same epoch identity with changed bytes is an identity conflict;
- same scoped registration operation with any changed client fact conflicts;
- two operation identifiers for equal source reuse one content epoch;
- registration failure cannot retain only an epoch, receipt, or audit event;
- concurrent attempts for one registration operation commit at most one receipt;
- update or delete of an epoch or activation row is rejected by PostgreSQL;
- two concurrent commands for one expected revision yield at most one success;
- `A -> B -> A` rejects a stale command prepared at the first `A`;
- audit idempotency conflict, cancellation, or SQL failure commits no pointer
  or activation record;
- a generic audit append cannot create an activation event;
- a runtime principal missing exact epoch-table privileges is unavailable;
- a retained but coverage-reducing, incomparable, or unproven epoch cannot be
  activated through rollback;
- an exact retry with a later server clock value returns the retained duplicate.
- a second concurrent policy admission is not queued or submitted;
- request cancellation releases local admission capacity and cannot publish a
  registration result;
- a broken admission worker reaches neither authorization nor persistence.

Bounded status and exact-source export are read projections over this state,
not lifecycle authorities. Status keyset-orders immutable epochs by `epochId`
and keeps mutable active revision on the active pointer. Source export filters
by repository scope before decoding, re-admits the retained draft, emits exact
bytes, uses the domain-separated source hash as `ETag`, and uses raw SHA-256 of
the response bytes for RFC 9530 `Content-Digest`.

## 7. Non-Claims

This module does not parse HTTP credentials, reload process wiring, interpret
planning coverage, make a planner decision, approve dynamic omission, or prove
backup, failover, provider execution, or production retention. The worker bound
is per backend process, not a deployment-wide CPU quota.
