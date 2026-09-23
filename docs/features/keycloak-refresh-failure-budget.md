# Keycloak Refresh Failure Budget

## Decision

`integrations.keycloak` limits sequential failed discovery and JWKS loads, not
only concurrent loads. Each cache admits a new attempt no earlier than 30
seconds after its previous producer failed. Direct requests and the existing
background refresher share that provider-owned interval. This refines the
[identity contract](../architecture/modules/control-plane-identity-and-repository-attestation.md)
and preserves [response-owned cache age](refresh-result-freshness.md).

The observable change is explicit: recovery after a short outage may wait up
to 30 seconds for another attempt. An unexpired, source-matching positive cache
entry remains available. Throttling never extends cache lifetime, trusts an
unknown key or turns dependency failure into invalid credentials.

The [implementation plan](keycloak-refresh-failure-budget-implementation-plan.md)
owns delivery and native proof. No logout or session-revocation policy changes.

## Why This Boundary

For fast failures, single-flight permits the sequence
`load -> fail -> clear -> load` for every caller. It bounds concurrency, not
frequency. A background-only retry interval does not cover direct requests.
The current background owner already uses 30 seconds after failure; applying
that interval at the shared cache owner covers all callers without another
queue, service, retry dependency or generic circuit-breaker state machine.

Discovery owns the provider bootstrap profile and the common failure interval;
JWKS and the background client import it. The successful unknown-key cooldown
remains a separate contract even though its present numeric value is equal.

## State And Invariants

Each existing cache retains at most one task, one positive snapshot, a closed
bit and one monotonic retry deadline. There is no per-caller failure ledger.
Let `F` be producer failure time, `R = F + 30`, and `K` the time at admission:

```text
NewLoad => not Closed and no PendingLoad and K >= R
ProducerFailure => R := MonotonicNowAtFailure + 30
CallerCancellation != ProducerFailure
PositiveRead => ExistingFreshnessAndSourcePredicates
DelayedSettlement => no renewal of R and no removal of another task
```

The producer sets `R` synchronously before returning its failure. No await
separates that sample and assignment. A caller that resumes later cannot
renew the interval. Existing locks serialize task selection; task-identity
checks protect publication and cleanup from late waiters.

When every waiter cancels, `shield` leaves the producer cache-owned. A later
selector retrieves its completed exception before replacing the task and
still checks `R`. Otherwise `aclose` awaits and consumes that task. The cache
does not create background exception observers or discard a pending producer.
Successful completed tasks retain their response-owned timestamps and are
settled through the existing freshness/source checks.

For failed attempts in one live cache, successive admission times satisfy
`A[n+1] >= F[n] + 30 >= A[n] + 30`. This is a process-local failure budget,
not a bound on successful explicit refreshes or an organization-wide quota.
Provider outage duration, process restart frequency and replica count are
external to this claim.

## Native Falsifiers

| Operand                   | Required observation                                                                                                      |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------|
| Deadline comparison       | No second provider call at `R-epsilon`; a new call at `R` and `R+epsilon`.                                                |
| Positive-cache precedence | Failed proactive refresh does not block a still-current admitted value; expired values remain unavailable.                |
| Producer-owned clock      | A delayed waiter cannot move the retry boundary to its own settlement time.                                               |
| Task identity             | An old failed waiter cannot clear a newer in-flight retry.                                                                |
| Cancellation              | All waiters may cancel before producer failure; later calls still obey the original interval and recover at its boundary. |
| Closure                   | `aclose` consumes retained producer completion and later entrypoints reject without I/O.                                  |

Tests exercise the actual cache entrypoints with a controlled monotonic clock,
counted transport calls and event barriers. Sleeps do not constitute race proof.
The JWKS expiry case keeps discovery fresh and source-matching. The delayed
settlement case starts another refresh after recovery without advancing time.
The orphan-close case has no intervening selector: a weak reference and the
public loop exception handler observe collection without retrieving the error.
The test releases its joined cancelled waiter first, so its cancellation
traceback cannot keep the producer alive after cache closure.
The handler is scoped and restored; unrelated loop errors retain their handler.
Native tests execute only in GitHub; static checks and Proofkit routing do not
prove these runtime claims.

## Alternatives And Revision Conditions

Immediate retry has lower recovery latency but no sequential amplification
bound. Per-call sleeping occupies caller resources and does not establish one
shared budget. A general circuit breaker adds half-open state and configuration
without a required extra behavior. A distributed limiter requires another
authority and deployment dependency for an unmeasured cross-replica need.

Two cache-local selectors and the existing background loop are sufficient for
this scope. Revisit the choice for provider `Retry-After`, changing recovery
SLOs, multiple issuers, distributed quotas or measured fleet amplification.
No global optimality, measured DoS resistance or production readiness is claimed.
