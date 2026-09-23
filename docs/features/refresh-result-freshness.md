# Response-Owned Verification Cache Freshness

Status: implementation refinement

Date: 2026-09-06

## Decision

The GitHub Actions JWKS, Keycloak discovery and Keycloak JWKS loaders bind
each admitted response to its monotonic acquisition time. A later waiter may
publish that immutable result but cannot renew its lifetime. Every successful
post-await return rechecks freshness; Keycloak keys also recheck the current
discovery URI. No generic cache abstraction or additional dependency is needed.

This refines [GitHub JWKS](../architecture/modules/jwks-provider.md) and
[control-plane identity](../architecture/modules/control-plane-identity-and-repository-attestation.md).
The [implementation plan](refresh-result-freshness-implementation-plan.md) owns
delivery; the [audit ledger](../adoption/temporal-and-oracle-audit-2026-09-06.md)
owns the other supplied findings. Predecessor documents remain unchanged.

## Problem And Minimal Model

Let `f` be response acquisition, `p` publication, `u` consumption, `T` the
existing cache TTL, and `U` the discovery-admitted JWKS URI at consumption.
All local durations use the injected monotonic clock; no wall-clock identity
is introduced.

```text
Before: refresh completes at f -> every waiter cancelled -> publish at p
        -> cached obtained_at := p -> accept while u < p + T

After:  refresh completes at f -> immutable result contains obtained_at = f
        -> any publication preserves f -> accept only if f <= u < f + T

KeycloakKeyAccepted(result, u)
  => Fresh(result, u)
  and DiscoveryCurrent(u)
  and result.jwks_uri = U
  and not CacheClosed
```

The acquisition sample occurs immediately after the bounded response is
returned and before synchronous decoding. The decoded result must still meet
all predecessor key, issuer, algorithm, destination and resource rules.

The counterexample is `p >= f + T` with `u = p`: the predecessor accepts an
expired response. Preserving `f` makes the two inequalities inconsistent, so
the corrected predicate rejects that trace. `u = f + T` is expired, not fresh.
This is an exact local age property, not an assertion about provider key
revocation before a fresh response can be obtained.

## Transition And Failure Semantics

1. A current matching snapshot is returned through the existing cache-hit
   predicate. Only one shared refresh may be in flight.
2. Cancelling a waiter does not cancel the shared task. A result may remain
   detached, but its acquisition time is immutable.
3. Only the exact current refresh task may replace the snapshot. Close is
   terminal; it cannot publish or return new trust.
4. A late result that is expired or bound to an obsolete/unavailable discovery
   source returns existing typed unavailability. It does not recursively start
   another fetch inside the same lookup. A subsequent eligible call may recover.
5. GitHub `probe` applies the same age rule as `get_key_set`; successful HTTP
   acquisition alone does not imply a usable cache at the probe's return.
6. GitHub's successful-refresh cooldown starts at acquisition. Delayed
   publication cannot invent a new cooldown. Existing failure backoff and
   Keycloak unknown-key intervals remain unchanged.

The URI check is a consistency refinement of the existing Keycloak cache-hit
rule: a concurrent discovery change must not make the post-refresh path weaker.
It does not add issuer generation IDs, global instantaneous revocation,
automatic retries, session invalidation or a new provider policy.

## Ownership And Alternatives

| Choice                                                     | Protected result and cost                                                                                                         |
|------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| Preserve timestamps in existing private snapshots          | Selected: three local state machines keep their own failure and source semantics; constant memory and no extra network round.     |
| Cancel shared refresh when any waiter leaves               | Violates surviving waiters and the existing single-flight contract.                                                               |
| Reset age at publication or periodically refresh sooner    | Does not reject the detached-result counterexample.                                                                               |
| Move every state transition into task completion callbacks | Can solve the issue, but adds a different lifecycle owner and callback error handling without a required observable benefit here. |
| Introduce a generic cache library/framework                | Does not by itself own source binding, typed failures or terminal close; greater migration/proof cost for this local repair.      |

Existing immutable dataclasses express private already-admitted results.
Pydantic remains appropriate at data admission boundaries; it does not decide
clock ownership or coroutine ordering. No manual replacement of an admitted
library validator is introduced.

Reconsider this choice if consumers require waiter-independent publication,
new source-generation semantics, different key overlap policy, or measured
availability loss from a bounded unavailable result. A new counterexample
reopens only the affected predicate; the decision is not a waiver.

## Acceptance And Limits

Native witnesses cancel every waiter, observe refresh completion, then vary
the use time before, exactly at and after expiry. They exercise every public
refresh-consuming entrypoint, positive cache reuse, next-call recovery, source
change during refresh, discovery expiry and terminal close. Event/task
completion observations establish order; sleep duration is not the oracle.

The acceptance basis also distinguishes these independent countermodels:

| Trace                                                                      | Forbidden implementation it rejects                                                                         |
|----------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| Failed refresh, then an eligible successful response                       | Retaining the completed failed task permanently. GitHub checks before and at the existing backoff boundary. |
| Two surviving waiters; the first successful return advances time to expiry | Returning an already-published result to later waiters without rechecking its age.                          |
| The real decoder completes after the response lifetime has elapsed         | Sampling acquisition time after decoding and renewing expired evidence.                                     |

Key-age isolation tests supply current discovery metadata through its public
read boundary so discovery expiry cannot mask a missing key-age guard. Separate
real discovery/cache interleavings retain URI-change and discovery-expiry proof.
The two-waiter schedules release I/O only after both public calls have reached
their shared pending refresh; first-return clock advancement precedes the next
consumer's age check. Native execution remains a separate GitHub obligation.

The existing strict JSON, key/algorithm, byte/count, transport, unknown-key,
rotation and cancellation witnesses remain. GitHub Full Check executes native
tests; local static gates establish only their own proof classes.

These obligations do not certify all authentication routes, deployment
capacity, runtime key custody, production admission or the complete external
audit. The [Python shield contract](https://docs.python.org/3.13/library/asyncio-task.html#shielding-from-cancellation)
supports why a cancelled waiter can leave a refresh running; the new tests
must establish this repository's behavior.
