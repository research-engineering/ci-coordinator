# Qualify Production Capacity

Status: owner-run how-to

Last updated: 2026-08-22

## Outcome

Produce environment-bound evidence that one deployment profile satisfies its
database, request, reconciliation, retention, and shutdown budgets. This
procedure does not make capacity a source-code constant and does not extrapolate
from local tests.

## Acceptance Identity

Freeze all inputs before execution:

```text
CapacityEpoch :=
  artifact digest
  + source commit
  + environment and deployment subject
  + database identity
  + runtime configuration
  + ingress, network, provider-capacity, and retention policies
  + replica and worker topology
  + exact CPU, memory, PID, and termination limits
  + workload model digest
  + measurement protocol and sample-set digests
  + audit-storage inventory digest
  + capacity-profile and budget-set digests
```

Evidence from one epoch cannot qualify another. A changed conjunct invalidates
the conclusion unless an owner-defined monotonic relation proves the new epoch
no weaker.

## Define Budgets

The service owner and platform owner must approve finite values for:

| Budget                | Required measurement                                                                                                                      |
|-----------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| HTTP admission        | offered rate, admitted rate, rejection rate, latency percentiles, retained bytes                                                          |
| Webhook normalization | body-size distribution, worker occupancy, busy rejection, CPU time, parent-side process-serialization latency, event-loop heartbeat delay |
| GitHub transport      | exchange concurrency, credential refresh wait, provider rate response                                                                     |
| Reconciliation        | subjects per round, round duration, retry delay, stale work count                                                                         |
| PostgreSQL            | transaction rate, lock wait, pool occupancy, query latency, storage growth                                                                |
| Audit and history     | rows per day, bytes per row, replay throughput, retention horizon                                                                         |
| Shutdown              | in-flight requests, drain duration, cleanup duration, forced termination count                                                            |

Each budget requires an alert threshold strictly below the hard resource limit.
An unspecified limit is an unresolved obligation, not an infinite budget.

## Exercise The Envelope

1. Restore a production-shaped database snapshot containing the approved
   retention horizon and synthetic non-secret payloads.
2. Start the exact immutable artifact with the frozen deployment topology.
3. Replay a workload that covers ordinary, burst, maximum-body, provider-slow,
   database-contention, reconciliation-backlog, and shutdown-under-load cases.
4. Increase offered load through the approved operating point and one declared
   overload point. Overload must produce bounded rejection or backpressure, not
   unbounded queues or process failure.
5. Run a full audit replay against the frozen ledger head and record bounded
   process memory, temporary-storage high-water mark, and completion time.
6. Verify retention deletion, database growth projection, backup duration, and
   restore duration against their owner budgets.
7. Stop the service under admitted peak in-flight work and prove termination
   within the container grace period without a partial durable effect.

## Retain Evidence

Retain immutable raw measurements, command versions, workload input digest,
database statistics, application metrics, provider response summaries, start
and end instants, and the complete acceptance identity. The measurement
completion time is `observedAt`; it must not follow receipt issuance. Derive the
conclusion from those artifacts in a separate canonical Ed25519-signed receipt
whose metric domain exactly equals the packaged
`capacity-qualification-profile.v1.json` domain.

```text
CapacityQualified
iff every declared budget passes
and no measurement is missing
and no overload case violates a safety invariant
and evidence identity = deployment identity
```

Any timeout, missing sample, truncated output, identity mismatch, or unexplained
error makes the result `UNPROVEN`. Local unit tests, static analysis, and a
successful smoke deployment are necessary witnesses but are not capacity
evidence.

The current repository supplies an offline decoder and verifier, not a private
key, measurement producer, signer service, or runtime startup integration. A
successful `CapacityQualified` value authenticates the supplied bytes and
their exact epoch; it does not establish measurement truth or authorize
deployment. Enforcing production evidence may bind the exact envelope digest
only after the deployment owner separately admits the producer and signer.
