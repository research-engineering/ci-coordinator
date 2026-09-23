# History Collection Stage Telemetry

Status: implementation design; native and live qualification pending

Owner: runtime observability, `REQ-CI-RUNTIME-015`.
Delivery: [implementation plan](history-stage-telemetry-plan.md).

## Decision

Measure claim, repository-access admission, provider read and durable completion
separately in each archive lane. Reuse the existing Prometheus histogram timer
and isolate its failures inside `RuntimeMetrics`. Do not change collection
concurrency, deadlines, transactions, leases, access checks or retained data.

The pilot exposes archive throughput but not its dominant cost. A bounded
database sample found idle connections; that does not exclude short expensive
queries or provider waits. Tuning a pool, adding a cache or introducing a broker
before attributing cost would therefore lack the evidence needed to prefer it.

## Contract

```text
lane = backfill | discovery | recent | repair | other
stage = claim | access | provider | completion | other
outcome = returned | raised | cancelled
```

`ci_coordinator_ci_history_stage_duration_seconds` records process-local elapsed
seconds using the pinned Prometheus timer's monotonic `default_timer`.
The [library timer](https://prometheus.github.io/client_python/instrumenting/histogram/)
and its public `labels()` API supply one independent timer per span. A private
structural protocol types its three unannotated methods without replacing them
or weakening the repository type-check policy.
Labels contain no repository, run, actor, path or exception text. At most
`5 * 5 * 3 = 75` label combinations exist, independently of repository count.
Fixed histogram buckets bound each combination's sample count.

`returned` means only that the await returned. It may represent denied access,
no due work, a deferred provider result or a lost completion claim. Existing
provider-result and durable-transition counters retain their separate meanings.
No duration is CPU time, billed time, a committed archive count or a savings
measurement. Overlapping lanes must not be summed as end-to-end elapsed time.

For each actual operation `O`, with dependency outcomes and scheduling fixed,
protected observations are its invocation count, arguments, order, returned
value, exception identity and cancellation:

```text
ObservationProjection(Instrument(O)) = ObservationProjection(O)
TimerFailure -> unreliable timing evidence, not retry(O) or suppress(O)
Cancelled(O) -> cancelled timing outcome and the same propagated cancellation
```

The wrapper catches only instrumentation exceptions outside the body. It adds
no await, deadline, task, retry, transaction or provider call. A per-invocation
timer prevents overlapping operations from overwriting another lane's start or
labels. Timer failures use the existing instrumentation-failure counter; a
missing sample never becomes a zero-duration successful operation. Exporter
updates are not transactional: an exception after a partial or complete update
can leave that update visible. Do not interpret a failure count as an exact
count of wholly discarded histogram samples.
Instrumentation consumes nonzero execution time inside existing deadlines;
this design does not prove unchanged timing at an arbitrarily close deadline
boundary. Measure its overhead before using these signals for capacity claims.

All claim and completion branches are covered, including `none_due`, deferred,
unavailable, skip and discovery handoff. Shared read helpers receive the lane
explicitly, never through mutable service state. Access and provider spans stay
inside their existing shared deadline, while completion stays outside provider
I/O and retains its existing durable authority.

## Alternatives And Reconsideration

- A bare library timer can replace an application exception with an exposition
  failure. The thin isolation wrapper is required by the existing non-authority
  contract; a new timing framework or custom clock is not.
- Per-operation hand-written timing duplicates clock and failure handling across
  branches. One observer method centralizes only measurement mechanics.
- Distributed tracing could later identify cross-service dependencies, but adds
  export, retention and privacy decisions not needed for this finite question.

After collecting a bounded interval, compare stage distributions, item/provider
outcomes and actual archived growth. Optimize the measured dominant stage while
preserving authority and fairness. Revisit when these four stages cannot explain
the cost or when measured instrumentation overhead is material. This change
does not claim root-caused readiness degradation, higher capacity or speedup.
