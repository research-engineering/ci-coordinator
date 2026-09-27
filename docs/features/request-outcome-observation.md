# Request Outcome Observation

Status: bounded implementation decision; native qualification required

## Ownership

The [observability module](../architecture/modules/observability.md) owns
loss-tolerant HTTP and plan projections; the
[SLO policy](../reference/service-level-objectives.md) owns populations and
thresholds. This design owns the temporal observation rationale, not a second
authorization, issuance, readiness or shutdown policy. Existing designs and
implementation plans remain unchanged.

## Decision

Reuse one typed request-local scope record in the existing outer HTTP observer.
The route marks parsed/trusted admission, explicit authentication dependency
unavailability, exclusions and an admitted Issued envelope. It does not count
a terminal result. Durable signed-envelope/fallback observations remain at
issuance, independently of response completion.

The request admission owner binds its existing work and hard timeout handles.
Only their expired state establishes ownership. The observer latches first
owned expiry at the successful completion cut or terminal unwind. Completion
requires returned start and final body send, or required final trailers.
No message copy, response emission, new timer, deadline reschedule, retry,
cancellation suppression, registry or domain dependency is introduced.

Explicit invalid/unauthenticated/forbidden/conflict exclusions retain precedence.
Otherwise eligible owned expiry is timed_out; unattributed cancellation is
cancelled; non-cancellation send failure is response_failed; unexpected failure,
unsupported outcome or envelope failure is internal_error. Typed dependency and
issuance unavailability retain their existing results. Completed admitted 2xx
issuance is good, including selected, FullCI and duplicate responses.
Pre-authentication errors without the typed dependency carveout remain
HTTP-only. Timely completion freezes the outcome before later cleanup; a late
response after suppressed owned expiry is not timely success.

The HTTP status means acknowledged start or null, never a fabricated 499/503.
Only completed issued plan responses contribute success latency, measured at
completion. HTTP counts and other-route duration retain their own scope.
ASGI send is server-side evidence, not client receipt.

HTTP and plan sinks remain independent, including plan-only applications.
One finalizer makes at most one terminal write attempt. Ordinary instrumentation
failure may lose an observation but cannot alter the primary response or error.
The logger's existing bounded sanitizer admits the new finite fields unchanged.

Unknown cancellation is outside GOOD/BAD, with explicit classification coverage
and excluded share. Zero classified traffic is no availability observation.
Burn selectors change together without changing any threshold, window, hold,
minimum volume or FullCI policy.

After existing metrics authentication, ordinary exposition failure returns
empty503/no-store with a class-only metrics_exposition diagnostic. Authentication
failure does no rendering; cancellation propagates. Shutdown docs distinguish
the existing server/cleanup/reserve partitions, and both long-lived container
recipes persist45 under their application30 assumption. Runtime budgets do not
change.

## Alternatives

Counting Issued in the route cannot distinguish envelope/send failure and
would double-count if combined with a finally counter. Inferring deadline
ownership from exception text or status fabricates cause. Another response
violates the existing start fence. A generic telemetry framework or second
error finalizer duplicates ownership; the scope record and outer observer
preserve the existing error/correlation/admission order at lower local cost.
Treating every cancellation as BAD invents attribution; hiding it without
coverage would overstate observed availability.

## Qualification And Boundaries

Existing HTTP, metrics, correlation and pinned native h11 server tests must
independently vary A/U/D/I, start/final-send completion, work/hard expiry,
ordinary error, send failure and external cancellation from valid positives.
Controls include suppression before completion, cleanup after completion,
lease reacquisition, no second send, metrics-only/plan-only/shared/separate
sinks, absent metrics, and primary failure preserved under instrumentation loss.
Promtool must prove every BAD result's population, burn and volume membership,
cancelled-only/empty no-observation, mixed coverage, resets and unchanged
success-only latency. Source/typing checks are not native execution.

No readiness facts/scheduler, database drain, provider work, queue/evaluator,
auth/planning/signing, cookie or correlation ownership changes. No measured
performance, SLO attainment, absolute OS cleanup bound or production claim.
Reopen only the affected premise after changed owner semantics, a causal native
failure or a newly admitted consumer/transport boundary.
