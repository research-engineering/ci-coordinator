# CI Economics Collection Proof Closure

Status: accepted design
Owner: `ci_economics`
Requirements: `REQ-CI-RUNTIME-038`, `REQ-CI-CORE-015`

## Decision

The public snapshot-recording result describes the collection transition:
`captured` or `claim_lost`. Internal evidence insertion retains its separate
`captured` or `replayed` result. Complete the PostgreSQL evidence for replay,
duplicate completion, expired-claim fencing, and both row-lock acquisition
orders without changing planning or retention policy.

This design amends only those clauses of the
[original collection design](ci-economics-and-regression-telemetry.md).
The [module specification](../architecture/modules/ci-economics.md) remains
the current capability contract. The
[implementation plan](ci-economics-collection-proof-closure-implementation-plan.md)
owns execution and acceptance.

## Validated Findings

The PR #119 report describes an older snapshot. On base `c32282d`:

- collection already emits bounded `deferred` item metrics independently of
  successful maintenance scheduling;
- the PostgreSQL retention test already follows capture, snapshot and child
  deletion, tombstone purge, failed re-registration, and a fresh positive
  control; and
- existing claim tests establish acquisition exclusivity and injected CAS
  rollback, but do not establish the two database row-lock interleavings of an
  expired holder and its reclaimer.

The first two findings are stale. The third is a proof gap, not evidence that
the production transition is incorrect. Coverage and CI conclusions are bound
to their tested commits and cannot establish a current source defect.

## Result Semantics

Let `E(s)` mean an immutable snapshot exists for subject `s`, `C(s)` mean its
collection state is captured, and `H(s, claim, K)` mean the claim owns the
current revision at database time `K`.

The production transaction has this order:

```text
lock collection row
-> admit exact claim
-> insert or validate immutable evidence
-> conditional captured-state update
-> commit both effects
```

The following result law applies specifically to the public
`TransactionalCiEconomicsStore.record_snapshot` operation, including its
unit-of-work exit. Private repository results are pre-commit outcomes:

```text
TransactionalCiEconomicsStore.record_snapshot(s, claim) = captured
  => committed(C(s) and E(s))

TransactionalCiEconomicsStore.record_snapshot(s, claim) = claim_lost
  => no mutation committed by this operation
```

The internal insertion result describes another relation:

```text
captured => new immutable evidence inserted
replayed => identical immutable evidence already present
conflict => same identity with different semantic evidence
```

An internal replay followed by a winning collection CAS still completes a
capture. Projecting the internal result directly into collection metrics mixes
two operation boundaries. Map either successful insertion result to `captured`
after the collection CAS succeeds inside the transaction. The public adapter
publishes this result only after commit and required cleanup succeed. An error
before commit must roll back both effects; an uncertain commit must not become
public success or an assertion that no effect occurred. This also preserves
idempotency when the evidence primitive is used more than once inside one
transaction.

Under the ordinary committed production path, `E(s)` implies `C(s)` until
retention deletes the evidence; captured states cannot be claimed again.
Consequently, `replayed` is not a normal public collection outcome. Remove it
from that public type and metric inventory. Keep application and observability
inventories separately owned and require their existing equality witness:
observability remains independent of business-capability imports.

## Database Interleavings

The current database adapter already requires:

```text
HolderWrite => exact revision, generation, attempt, policy, owner, token,
               acquisition time, expiry, and statement time K < expiry
Reclaim     => exact prior state and revision, K >= expiry
```

Both writes lock the same row. At any database time, `K < expiry` and
`K >= expiry` are disjoint. A successful transition advances the revision, so
the prior claim cannot regain authority after commit.

The PostgreSQL witnesses must cover both serialization orders:

| First row-lock owner | Required observation                                    | After release                                        |
|----------------------|---------------------------------------------------------|------------------------------------------------------|
| Expired holder       | Reclaimer skips the locked row; holder cannot complete  | Reclaimer obtains a new generation and can capture   |
| Reclaimer            | Stale holder is observed waiting for that database lock | Reclaimer commits; stale holder returns `claim_lost` |

Each actor uses an independent unit of work and real runtime database role.
The second scenario observes actual PostgreSQL lock blocking, not merely task
creation order. Synchronization is bounded by an explicit test deadline. Each
concurrent witness owns its tasks through a `TaskGroup`, so exceptions and
cancellation join every child before the surrounding engine is disposed.

Inject failure at the public adapter's commit boundary both before durable
commit and after commit but before acknowledgement. Neither path may return
`captured`. Independent connections must observe no effects in the first case
and both effects in the second; a subsequent completion must respectively
succeed or lose the old claim. This tests the application boundary without
pretending that every unavailable result implies rollback.

Long production lease durations do not justify wall-clock sleeps in tests.
Create a real claim, then translate its whole persisted time epoch and matching
credential into the past in one privileged test transaction. Preserve time
differences, subject binding, policy, revision, generation, and owner. Verify
normal trigger mode and the expired but still collectible state before racing.
Only this fixture preparation uses the administrator connection.

## Scope And Alternatives

| Choice                                          | Decision and reason                                                                        |
|-------------------------------------------------|--------------------------------------------------------------------------------------------|
| Add a shared metrics/domain package             | Reject: existing owner boundary and equality witness already close the relation            |
| Treat internal replay as an invariant failure   | Reject: replay is a valid evidence primitive and need not create a new failure mode        |
| Use concurrent tasks without proving lock order | Insufficient for the two named database interleavings                                      |
| Sleep for a full lease                          | Reject: translated fixture epochs preserve the relevant predicates at lower execution cost |
| Change production lease or retention policy     | Unnecessary without a failing behavioral counterexample                                    |

Extract only the existing database scenario factories needed by both test
modules. Keep schema/ACL/retention tests in their present file and put the new
claim/replay interleavings in a dedicated test module. The split follows reused
fixture ownership and scenario purpose, not a line-count verdict.

## Falsifiers And Limits

Reject the implementation if a stale holder commits evidence, a successor
claim does not advance fencing fields, replay changes retained bytes, unequal
evidence is accepted, two completions both capture, a surviving lock or task
escapes the test deadline, or a metric can classify internal replay as a public
collection outcome.

The tests establish these bounded PostgreSQL interleavings and preserve the
existing complete retention lifecycle. They do not prove every possible
schedule, provider availability, CPU savings, or production readiness. Revisit
the public result algebra if a new supported collection path can replay a
completed operation as a separately meaningful product outcome.
