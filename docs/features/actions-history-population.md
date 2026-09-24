# Actions History Population

Status: implementation design; deployment pending

Owner: `ci_economics`. Retention semantics remain owned by
[Actions history retention](actions-history-retention.md); this document owns
historical enumeration and the distinction between traversal and completeness.
Delivery: [implementation plan](actions-history-population-implementation-plan.md).

## Decision

Reuse the bounded provider discovery protocol inside a resumable historical
cursor. Keep historical storage independent of the active-evidence collection
queue. A scan page is not a transaction spanning provider I/O.

```text
authorized scope + frozen source interval
  -> bounded interval/page cursor
  -> observed run and latest-attempt ceiling
  -> one exact attempt at a time
  -> permanent attempt/job contributions + optional detail
```

One scan never enumerates an unbounded Python list. One provider request uses
the existing seven-day maximum and 100-row page. Bisect an overflowing window
to handle the provider's 1,000-result search cap. Overall history has no age-based cutoff;
its lower bound comes from repository creation metadata or an explicit bounded
administrator request, not from the active-evidence registration window.

The [installation repository catalog](https://docs.github.com/en/rest/apps/installations#list-repositories-accessible-to-the-app-installation)
may expose an admitted repository creation timestamp as a
UI shortcut for that explicit request. The timestamp is advisory: missing or
malformed metadata cannot authorize a guessed bound, and a creation date does
not prove that GitHub still retains every run. The administrator must select
the shortcut and save the configuration; a deep link without catalog metadata
continues to accept a manually entered date. The UTC calendar day is rounded
down to midnight so a run created earlier on the same day is not omitted.
Selection is scoped to the current repository and browser authority revision.

## Frozen Identity And Progress

The historical cursor binds exact installation/repository scope, the requested
inclusive second-precision interval, cycle start, active window and page.
The cycle start cannot precede its upper bound. Resume serializes these fields;
restarting a worker does not choose a newer upper bound or reset progress.

Advance only an admitted page with matching scope, interval and page number.
Use the same provider page admission, not the six-hour continuous-observation
scheduling policy. Start with a window of at most seven days and bisect it when
the observed count exceeds the cap. After a window ends at second `t`, begin
the next at `t`. Query bounds have second precision, but source timestamps need
not. Overlapping closed intervals preserve coverage even for a source at
`t + 0.5 seconds`; advancing to `t + 1 second` would lose it. A window at most
one second wide is irreducible under the query grammar. Durable attempt identity
deduplicates shared boundaries, rescans and pagination changes. Formally,
`[a,b] union [b,c] = [a,c]`; duplicates are cheaper than an unproved precision
assumption and cannot create a second statistical contribution.

For a fixed interval and fixed provider population, each accepted transition
either consumes a bounded page, narrows a saturated interval, advances the
completed timestamp or terminates. At the minimum splittable interval,
saturation becomes an explicit gap, not an infinite retry and not completeness.
An irreducible saturated window still consumes each available page up to the
provider's1000-result ceiling. Only its final permitted/terminal page records
the truncation gap and advances the outer window; saturation does not discard
pages 2-10. The discovery adapter admits this 100-row response under the existing
8 MiB transport cap, while single-object response admission stays unchanged.
The [recovery supplement](actions-history-recovery-hardening.md) supplies the
counterexamples, limits and native acceptance plan.

The cursor is a value, not write authority. Durable integration must additionally
check current authorization, configuration/dataset generation, lease and CAS
revision after the database lock. Pause or erasure invalidates old writers.

### Explicit Earlier-Bound Expansion

An administrator may explicitly move the configured lower bound to an earlier
whole UTC second. This is an existing-dataset configuration command, not a
provider-discovered creation date or an implicit effect of ordinary rescan.
It requires the exact current configuration revision, a new operation ID, the
same authorization and audit transaction, and a bound strictly earlier than
the current backfill cursor's `created_from`. Moving the bound forward or
reusing the operation ID for different bytes is rejected. Expansion and the
ordinary rescan flag are mutually exclusive; expansion itself starts a new
backfill cycle from the earlier bound through the current database time. The
submitted selection, enablement, retention and quotas must equal the current
configuration; changing them is a separate operation with its own revision.

Expansion requires `checkpoint.pending = null` under the same SQL lock.
Otherwise it returns an explicit `pending_work` conflict and commits no audit,
configuration or scan effect. A pending page can contain multiple discovered
runs that expansion must not discard. Existing rescan and selector-change
semantics are unchanged in this batch; their recovery guarantees remain an
independent qualification concern. A leased worker with no committed pending
page is fenced by the incremented scan/configuration revision at terminal CAS.

The existing dataset generation, accumulated statistics, first-import clocks,
stored configuration and recent-discovery frontier remain unchanged. A
separately governed default retention policy may still change independently;
the expansion does not freeze it. Configuration and scan revisions advance
atomically, fencing old claims; replay of an
acknowledged operation remains a historical receipt, not new activation.
Fresh success requires the atomically written backfill scan to start at the
requested earlier bound; the mutation response names the dataset revision,
while the subsequent coherent status read exposes the scan bound. It does not
claim a provider snapshot or successful traversal.
For a still-active or paused dataset, replay also checks that its current lower
bound has not moved forward beyond the admitted expansion. Commands without
expansion retain their previous canonical digest bytes: omitted and explicit
`null` both mean no expansion and the new field is omitted on serialization.
The existing timestamp admission also rejects invalid calendar dates and
fractional seconds for the new bound. Duplicate provider
observations remain one contribution. An uncertain write is retried only with
the identical command; a known rejection permits a fresh read and new command.
A new cycle
may reread already covered history: this is an explicit, potentially costly
administrative action, not ordinary maintenance. A future targeted older-only
segment would require additional persistent population state and must first
show a measured benefit that justifies that new authority.

## Attempt Population

The run listing exposes a current attempt number, not all past attempts.
Freeze that positive ceiling for the discovered run. Enumerate integers
`1..ceiling` one at a time through the exact-attempt endpoint. Do not allocate
`range(ceiling)` as a materialized collection or impose a silent attempt cap.

Each attempt response must match installation/repository/run/attempt. Its own
provider response supplies the head and timestamps. A larger attempt observed
later belongs to a subsequent reconciliation; a changing current-run response
cannot move this work item's ceiling. Duplicate delivery cannot advance an
already consumed attempt cursor.

Missing attempts, denied access and malformed responses retain distinct
outcomes. In particular `404` does not prove deliberate deletion, and the next
attempt may be visited only after durable storage of the missing-attempt gap.
Provider availability never authorizes deletion of local history.

### Exact Attempt Reads

The archive adapter uses the installation-scoped transport and resolves the
repository by numeric ID before an attempt-specific request. Bind method,
operation, path, query, request/response API versions and HTTP outcome before
interpreting bytes. Only a matching `404` for that attempt is a missing-attempt
observation; repository lookup failure or an unrelated response cannot produce
it. Missing job data retains the valid attempt header with unknown population.

Reuse the existing job-page decoder and pagination admission. Retain at most
2,000 terminal jobs, in 100-row pages, with a 1MiB response bound. A cap produces
a partial population, never an exact aggregate. Reject duplicate job IDs,
foreign run/head or a contradictory optional `run_attempt`, malformed pagination
and changing totals. Unknown job creation time stays null; never substitute a
run timestamp. Strip raw actors, runner names, logs and arbitrary provider keys
from the permanent payload. Workflow blob identity remains unknown.

Require terminal run/job observations and matching admitted run-header reads
around job enumeration. This detects observed instability without claiming a
snapshot-isolated GitHub transaction. It is less costly than reading all job
pages twice, while remaining separate from current omission/evidence admission.
The terminal archive is informational; active-run retry/fairness and continuous
recent collection are scheduler obligations, not reasons to widen its meaning.
Static adapter acceptance does not close those scheduler obligations.

## JSON Admission Boundary

Archive Python inputs require exact aware datetime values and tuples. Their
JSON representations use timestamp strings and arrays. The inherited strict
instance-revalidation wrapper must remain: it rejects forged nested instances
and undeclared fields. On the pinned Pydantic version, however, that wrapper
prevents automatic datetime/tuple JSON admission, reproduced by native archive
round-trip tests and described in [upstream issue12236](https://github.com/pydantic/pydantic/issues/12236).

Normalize only exact JSON strings through the library datetime adapter and
exact JSON arrays to tuples, using validation-mode-aware field validators.
Preserve strict numeric/boolean rejection, timezone checks, alias admission,
unknown-field rejection and Python-mode types. The conversion is a wire
representation boundary, not permission to coerce arbitrary inputs. Reuse the
same timestamp type for archive lease and scan payloads. Round-trip and
per-operand negative witnesses cover both modes. A global lax model, a second
payload hierarchy or a core-schema override has greater behavioral scope than
these two conversions. Remove them only when the pinned library plus unchanged
native witnesses proves native JSON handling preserves every protected rule.

## Scheduling And Recovery

Keep the existing historical cursor as the bounded backfill traversal. Add one
bounded PostgreSQL recheck queue per admitted dataset for recent run hints and
localized historical failures. Reuse the same exact-attempt provider; neither
an event hint nor a prior failed attempt is itself a statistical contribution.

A single pending historical attempt is not sufficient for continuous capture:

```text
one cursor and persistent failure at attempt A
  => later attempts cannot progress

long backfill and only a terminal recent-history frontier
  => new runs may remain uncaptured throughout backfill
```

The queue separates those obligations without another service or broker. A
transient historical attempt failure immediately transfers the exact attempt
to recheck work. Advancement requires an atomic durable gap plus admitted
recheck work. If either budget is exhausted, preserve the checkpoint and report
capacity. Never silently skip work to make a progress indicator advance.
An exact missing-attempt response remains a recorded availability gap, not a
claim of deletion or an endless retry of a removed provider resource.

Queue identity is scope, dataset generation and workflow run ID. One record
holds a numeric pending attempt interval, workflow ID, source creation time,
revision, next eligible time and a hard lease. Duplicate hints do not reset a
retry deadline or create a second record. A genuinely expanded interval
increments revision and invalidates an outstanding claim; a narrower duplicate
does not. Conflicting workflow/source identity is rejected. A completed request
is removed by the winning fenced transition; replay can use already archived
complete facts without renewing detail lifetime. Explicit rescan may reread
them and remains non-destructive.

Use indexed due-time selection with `SKIP LOCKED` and a finite candidate/read
bound. Failed rechecks receive bounded backoff with jitter so one run cannot
hold later runs. Each pending attempt gets at most three budgeted acquisitions,
including workers that crash after claiming. The last failed or abandoned acquisition
requires a durable exact-attempt gap before advancing to the next attempt.
An expired third lease can only be finalized by a fresh fenced recovery
transaction, never the expired holder. Thus one bad attempt cannot indefinitely
hold the other attempts of its run. This is bounded collection, not guaranteed
eventual provider recovery: an explicit rescan or a later hint can retry gaps.

A confirmed storage-capacity rejection is different from provider failure.
After rolling back the rejected contribution, a current holder may commit a
capacity deferral: preserve the pending attempt, refund only its own acquisition
debit and schedule a bounded delay. This transition remains subject to the
same final SQL lease check. A duplicate completion cannot refund twice. A crash
before this durable transition still consumes its debit. The stored counter
therefore represents outstanding retry-budget debits, not lifetime provider
calls; telemetry must count calls separately. Storage capacity may block
progress until quota or occupancy changes and must remain visible. No gap or
completion is manufactured to bypass that capacity.

Keep the originally admitted lower attempt bound as well as the next attempt.
A duplicate interval must not rewind completed progress. An ordinary hint may
rewind only for an extension below that original bound; an exact current-parent
historical repair may also revisit its transferred completed attempt under the
[atomic handoff contract](actions-history-storage.md#historical-repair-handoff).
A higher ceiling preserves the current
next attempt and its retry budget. Promotion from repair to recent also
preserves retry count and due time. Any changed row revision invalidates the
old claim. Acquisition captures the current configuration revision separately;
changed configuration invalidates completion even if the queue row is unchanged.

The queue is bounded separately from permanent statistics:
at most 256 pending runs per dataset, with at most 128 occupied exclusively by
backfill repair. Thus historical failures cannot consume the recent-hint
reserve. A recent hint may promote an existing repair entry without resetting
its attempts or deadline. Reserve is admission policy, not a promise to absorb
unbounded provider traffic. Full admission records visible degradation and
never reports successful collection.

Historical traversal and recheck collection receive separate bounded turns
within the existing maintenance service. Its runtime callback drains each lane
independently for at most 16 items inside one 45-second budget; the existing
single-turn operation remains one item per lane. A slow sibling cannot hold
the next item of a fast lane, and empty/capacity/failed/aborted work stops that
drain. Every provider operation has a whole
attempt deadline below its lease lifetime; PostgreSQL transactions surround
claim and completion separately, never the provider call. Completion rechecks
generation, configuration, current selection, exact queue revision and lease
at SQL CAS. Statistics, optional detail, quota and queue progress are atomic.
Pause and erasure invalidate both sources of write authority.

Recent provider discovery and verified completion events may enqueue hints.
The existing bounded recent overlap reconciles missed events. A rerun of an
old workflow run needs its event hint or an explicit historical rescan:
filtering by run creation time cannot discover it reliably. Queue admission
and observation outcomes remain separate; an unavailable archive must not
silently change active-evidence or FullCI behavior. Before enabling continuous
archive controls, bind a durable hint-delivery path or report the missing
reconciliation obligation. Do not describe a best-effort post-commit callback
as reliable delivery.

A repeated full historical scan is rejected as ordinary maintenance: its cost
grows with repository age. Two timestamp cursors alone do not fix old-run
reruns or per-attempt failure starvation. The active-evidence queue cannot be
reused by weakening its source-time admission. A dedicated bounded SQL queue
is justified by these distinct existing obligations; reconsider it only if a
provider offers an equivalent durable incremental feed or measured demand
permits a simpler mechanism without weakening recovery.

## What Completion Means

### Application Collection Boundary

The application executes one backfill claim, one recent claim and one repair
claim concurrently per round. These fixed lanes prevent a slow provider read
in one source from serially blocking the other sources. A lane consumes at
most one claim per round; this bounds immediate work without claiming that
arbitrary incoming traffic can always be drained. No new scheduler service is
needed. Revisit the fixed limits only against measured provider and DB budgets.

Each read has a 20-second monotonic timeout including repository authorization,
repository resolution and all attempt/job/header reads. It is shorter than the
60-second database lease; this reserve does not replace the final SQL-time
authority check. Claims and completion use separate transactions. A cancelled
operation propagates cancellation, and an observed abort prevents the next
provider or completion effect. Abandoned claims remain subject to lease recovery.

The application validates exact page or attempt binding before calling storage.
Storage independently revalidates current generation, configuration, selection
and lease. A provider missing-attempt result must carry the exact requested
cursor. A valid partial/unknown population remains informational, not success
evidence for CI. A scope/identity mismatch is never sent to the statistics writer.

Two fixed-cardinality counters preserve independent facts: provider result and
durable work-item transition. For example, `provider_unavailable` followed by
`applied` means an acknowledged deferral, not successful statistics collection.
Provider binding/malformed/incomplete failures map to the existing durable
`provider_malformed` category; not-terminal/unstable failures are retryable
`provider_unavailable`. The more specific provider reason remains observable.
No repository IDs, provider messages or unbounded labels enter these counters.

Native acceptance covers each source/result identity operand, failure/capacity/
claim-loss outcomes, abort/cancellation boundaries, access and provider timeouts,
the three-lane concurrency bound and independent metric projection. Native
tests execute in GitHub; static admission does not prove this lifecycle.

### Traversal Claims

An exhausted cursor means the requested interval has been traversed through
the admitted provider responses. GitHub pagination is not a snapshot-isolated
database scan. Same-sized changes between pages can omit or duplicate a run
without changing `total_count`. Therefore:

```text
TraversalFinished != ProviderSnapshotComplete
ProviderMissing != DeliberatelyDeleted
ArchiveRecorded != CurrentCiEvidence
```

Store observed gaps and scan coordinates, reconcile a bounded recent overlap,
and offer an explicit non-destructive rescan. A rescan preserves contribution
identity and first-detail-import time. Do not display a global completion
percentage when its denominator is unknown. A future consistency mechanism
must not retroactively strengthen an earlier coverage claim.

## Storage Integration

The first implementation preserves compact facts at attempt and job grains.
The provider job ID identifies one execution; job/category trends require a
separate versioned cohort mapping. Unknown mappings remain queryable.
Attempt occupancy and included job occupancy are not additive populations.
CPU remains unknown unless separately measured and bound to the exact attempt.

The first statistical payload is closed and versioned. Retain installation,
repository, run, attempt and head identity; workflow ID/path, separately proven
workflow blob identity or unknown, trigger event, run conclusion or unknown,
source creation time and explicit job-population quality/count. A job retains
provider ID, display name, conclusion, nullable creation/start/end instants,
bounded runner labels and nullable numeric runner/group IDs. Runner display
names, steps, logs and arbitrary provider keys are not permanent statistics.
Existing strict attempt identity is reused without changing its public report
schema. Pydantic owns scalar and relation admission; it does not grant storage
or provider authority.

`complete` requires an exact declared count, including a genuinely empty
population. `partial` retains a strict subset or an unknown remainder, including
zero observed jobs when a positive provider total is known;
`unavailable` does not invent a count; `conflict` prevents an exact aggregate.
Preserve ordered unique job IDs. Display names are not logical-job keys,
workflow head SHA is not a workflow blob hash, and runner IDs alone do not
prove comparable capacity. Versioned cohort/category admission remains a
separate operation; unknown cohorts must remain visible.

The archive must have no deletion cascade from evidence, source or tombstone
tables. Its unique scope/run/attempt contribution and child job identity are
the replay guard. Optional detail expiry cannot remove statistical operands.
The first successful detail import and applied policy survive detail cleanup.

Before enabling writers, qualify bounded canonical bytes, attempt/job quotas,
lock order, forward migration, runtime grants, dataset erasure fencing and the
full public-adapter lifecycle. A pure cursor test cannot discharge these gates.

## Alternatives And Revision Conditions

Increasing the current six-day backfill bound is rejected: old source
registration still rejects it, while weakening registration would conflate
historical observations with current evidence. Fetching one enormous provider
page or replaying all history after every webhook violates resource budgets.
A second general-purpose scheduler duplicates existing bounded work ownership.

The chosen cursor reuses current discovery contracts without widening their
admitted interval. Inheriting six-hour scheduling costs up to 28 empty queries
for a week that one permitted query can traverse. A separate small history
transition is preferable to widening the live-observation cursor or introducing
a generic scheduler configuration and compatibility migration.
Reconsider it if the provider supplies a stable incremental cursor.
Any replacement must preserve population, explicit gaps, restart behavior,
authorization and bounded work; fewer requests alone are insufficient.

## Provider References

The run listing's filtered-search cap and exact-attempt endpoint are documented
in [GitHub workflow runs](https://docs.github.com/en/rest/actions/workflow-runs).
Attempt-specific job reads are documented in
[GitHub workflow jobs](https://docs.github.com/en/rest/actions/workflow-jobs).
These establish provider interfaces, not historical completeness or deployment.
