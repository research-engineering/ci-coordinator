# Bounded Runtime Recovery

Status: implementation refinement

Date: 2026-09-06

## Decision And Scope

Remove two avoidable waits in existing runtime boundaries: a production receipt
must not wait for a FIFO writer before rejecting the file, and maintenance must
not require every later primary reconciliation round to succeed. Keep one
scheduler, existing operation deadlines, exact failures and startup authority.

This successor refines [production admission](../architecture/cross-cutting/production-admission.md),
[runtime composition](../architecture/modules/runtime-composition.md) and the
[economics maintenance lifecycle](ci-economics-and-regression-telemetry.md).
The [implementation plan](bounded-runtime-recovery-implementation-plan.md) owns
delivery. Neither repair changes planning, signature admission, retention ages,
database authority or deployment approval.

## File Admission

The existing reader opens a path, validates the same descriptor, reads a bounded
payload, rechecks its identity and closes it. Opening a writerless FIFO with
`O_RDONLY` can wait before the file-type check is reachable.

```text
open(O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK)
  -> fstat(descriptor)
  -> reject unless regular and within size bound
  -> bounded read -> descriptor revalidation -> close

WriterlessFIFO -> OpenDoesNotWaitForPeer -> NotRegular -> TypedRejection
StableAdmittedRegularFile -> SameBytesAsBefore
```

Adding the operating-system flag preserves the descriptor-based authority;
checking the path before opening it would introduce a replacement race. A new
filesystem abstraction is not justified by this single missing flag. The
runtime secret-file reader has a similar mechanism, but its permissions and
encoding policy are not imported into receipt admission.

The bound is FIFO peer-wait avoidance, not a deadline for pathname resolution,
stalled regular/network filesystems, device drivers or a hostile privileged host.
The existing typed failure, size, leaf-symlink, identity and descriptor cleanup
rules remain unchanged.

## Maintenance State And Scheduling

Let `A` mean the initial primary round succeeded, `P` the current primary round,
`M_i` an existing maintenance operation and `D_i` its existing deadline.

```text
not A: await P; success sets A; failure/cancellation leaves A false
       no M_i may start

A and not abort: run P and the finite M_i set concurrently
                each M_i retains its own D_i and outcome
                preserve P's exact failure after owned children settle

A and abort: start no M_i; preserve the existing primary abort protocol
```

The primary executes in the parent coroutine. Ordinary primary exceptions are
diagnosed immediately, retained while bounded children settle, then rethrown.
Cancellation is not converted into a saved ordinary error: `TaskGroup` cancels
and drains children before the parent exits. Maintenance failures retain their
existing bounded metrics and cannot synthesize primary success.

Previously `Start(M_i) => Success(P)` held for every round. After initial
admission, an immediate primary exception or a primary waiting on a maintenance
event no longer prevents `M_i` from starting. The original exception is still
the round outcome, so maintenance progress does not imply reconciliation health.
This is a scheduling property, not proof of eventual physical deletion without
database availability, cooperative cancellation and future scheduler rounds.

## Costs And Alternatives

- The finite task bound becomes one primary plus at most four distinct existing
  maintenance operations. Pool sizes, provider bulkheads, leases and operation
  deadlines are unchanged. Real contention and capacity remain qualification
  obligations, not consequences of this static bound.
- A failed round's final publication may wait for the remaining maintenance
  budget. Its primary diagnostic is immediate; readiness follows the existing
  completed-round observation. No new immediate-failure readiness SLO is claimed.
- Newly terminal subjects can be collected on the next round. Existing durable
  claims and idempotency already admit concurrent replicas; this change does not
  rely on local serial ordering as database authority.
- Running cleanup in a second scheduler adds shutdown and health ownership.
  Running it only in a primary-error `finally` still couples its start to a
  slow primary. A new job framework or result protocol solves no additional
  admitted problem. The existing `TaskGroup` is sufficient for this boundary.

Reconsider the decision if an owner requires immediate primary-health
publication, independently periodic cleanup during an indefinitely unsettled
round, or measured contention violates a quality budget. Such a requirement
needs explicit lifecycle and capacity admission rather than a hidden task.

## Acceptance

Native witnesses must reject a real writerless FIFO in a bounded, killed/reaped
child while retaining a positive regular-file witness. Maintenance witnesses
must distinguish startup from later rounds, make a later primary wait on a
cleanup-owned event, retain exact ordinary errors, propagate external and
primary cancellation after both sides enter, and suppress new maintenance when
abort is already set. Existing per-operation failure/deadline and scheduler
readiness tests remain required.

An ordinary-failure witness also holds a child until the primary diagnostic is
observed, requires the public round to remain pending, and then requires normal
child completion. Cancellation assertions must reject an expired watchdog as
their source. Failed/cancelled startup is followed by a successful primary-only
retry and then a maintenance-enabled round, so repeated immediate cancellation
cannot hide premature startup admission.

Local static gates establish source, route and type properties only. GitHub
executes behavioral and integration witnesses. This document does not assert
production deletion lag, deployment readiness or complete external-audit closure.
