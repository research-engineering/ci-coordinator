# Concurrent Archive Reads

Status: implementation design; native and deployed acceptance are separate.

Owner: CI economics, with persistence and operator UI refinements.
Delivery: [implementation plan](concurrent-archive-reads-plan.md).

## Intended Delta

The existing analytics design rejects any full-dataset change between its
READ COMMITTED statements. An ordinary import can therefore prevent a useful
report, or make intermediate counts disagree before the final guard. The UI's
Refresh action updates history status but not its independent report request.

Replace that read protocol with a single statement snapshot for dataset,
purpose settings, population, limits and daily aggregates. Preserve the existing
UoW authority, READ COMMITTED isolation, application/SQL deadlines and budgets.
The returned revision identifies the observed snapshot; it does not promise
that no later write exists. Do not hold the dataset write lock or retry until
ingestion stops. PostgreSQL provides a consistent committed view for one plain
SELECT, not for several successive statements
([transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html)).

For snapshot S and admitted query Q:

```text
Available(Q) => Dataset(S) and Purpose(S) and Population(Q,S)
                and Buckets(Q,S) and BudgetAdmitted(Q,S)
ConcurrentCommit(after S) does not invalidate S
GenerationMismatch(S,Q) => unavailable
```

An initial purpose mapping may construct the bounded SQL expression only if
its exact canonical stored value and revision match the same-statement row.
Mapping change may refuse once; ordinary statistics writes do not. Canonical
dataset/settings validation remains mandatory. A metadata row is not replaced
with the earlier seed, and missing/empty cohorts remain distinct from zero
measurements. Corrupted population relations remain errors, not partial success.

## Refresh And Reuse

One user refresh intent reaches the displayed analytics request and status.
Preserve committed filters, reject stale responses after query/scope changes,
and do not replay mutations or restore expired authority. A generation change
still replaces the old scoped state. Tests observe the actual request/result.

Implement a bounded discovery-first refinement of the complete-fact shortcut
in [recent recovery](actions-history-recent-recovery.md), before queue admission:

```text
Replay => CurrentDiscoveryClaimAtCAS and CurrentActiveDatasetAndSelector
          and PendingInterval = [ObservedAttempt, ObservedAttempt]
          and NoScopedRunRecheckRow
          and ExactRetainedCompleteCanonicalFact
```

The persisted producer page already contains exact attempt/head/workflow/time
identity. Under the current producer, the singleton case is attempt 1. Check
absence of any scoped run queue row, without due/lease/source filtering, while
holding the existing scope lock. This prevents bypass of pending repair intent,
including legacy recent/repair merges. Decode full bounded canonical statistics
and jobs; count/digest/header existence alone do not establish completeness.
Reuse advances the same discovery checkpoint with the existing `replayed`
outcome and final lease/CAS check. It writes no statistical contribution, counts
no provider attempt read, and does not change first-import or detail clocks.

Unknown identity, queued work, partial/conflicting facts and explicit refresh
retain the ordinary provider path. Controlled canonical validation failure is
a miss, so a corrupt cached fact cannot strand discovery before the recovery
queue; database failure and cancellation still propagate. Backfill/rescan never
uses the shortcut. A repair admitted after replay still creates its work under
the same scope lock. Presence of attempt N does not prove attempts 1 through N.

The generic queued and multi-attempt shortcut remains explicitly deferred.
It needs durable head identity plus independent refresh intent and a compatible
codec/capability transition: old readers reject new unknown fields. Adding that
migration merely to optimize already complete single attempts is not required.
Delivery-inbox reuse is also deferred: its richer observations can contradict
the retained fact and its 100-item transaction needs a separate validation budget.
No SQL schema, capability identifier, queued encoding or public enum changes.
This is historical fact replay, not a fresh GitHub observation or CI authority.

## Alternatives And Acceptance

More retries retain the write-quiescence dependency. Dataset locks block
ingestion. A new cache duplicates freshness/lifecycle authority. A specialized
read-only repeatable-snapshot UoW is viable, but broadens transaction admission
and proof compared with one composed SELECT using existing query builders.
Choose the single-statement route while it meets current query/row/byte bounds;
reconsider if representative query plans or bounded native measurements regress.
No global optimality or production-capacity claim follows from this choice.

Writer readiness binds persistence to metadata, canonical bytes, mapping,
counts, daily rows, scope/generation, deadlines and unchanged transaction
authority; UI to request identity, refresh and cancellation; reuse to identity,
repair intent, complete population, lease/CAS and retention clocks. Their
derived API/schema/Proofkit/test surfaces must remain synchronized. Independent
operand substitutions and real PostgreSQL interleavings validate these relations.

The native budget oracle adds a division by `random(0,0)` to the daily projection.
This PostgreSQL overload returns an execution-time zero for every admitted draw;
it prevents planning-time constant folding, not probabilistic test success
([mathematical functions](https://www.postgresql.org/docs/18/functions-math.html)).
Each overflow guard must suppress that projection, while its deliberately
enabled control must report division-by-zero SQLSTATE22012. Fencing witnesses
substitute valid encoded dataset generation/state after preparation; they do
not claim an implemented public erase/reopen lifecycle.

This explicitly changes analytics availability semantics, not metric meanings,
access, retention, planning, consumer workflows or production omission.
