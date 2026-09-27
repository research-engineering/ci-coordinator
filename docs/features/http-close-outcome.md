# Retained HTTP Close Outcomes

Status: selected design
Date: 2026-09-27

Owners: [runtime composition](../architecture/modules/runtime-composition.md),
[GitHub](../architecture/modules/github-integration.md),
[Keycloak](../architecture/modules/control-plane-identity-and-repository-attestation.md),
and [Actions JWKS](../architecture/modules/jwks-provider.md).
Delivery: [implementation plan](http-close-outcome-implementation-plan.md).
Execution remains under CI-054 in the [roadmap](../../ROADMAP.md).

## Problem And Decision

The pinned httpx2 client marks itself closed before awaiting transport cleanup.
A partial transport failure followed by another client close can therefore return
success without retrying the unfinished resource. Wrapper retry cannot establish
physical release from that no-op result.

Each of the three owned HTTP clients retains one shielded close task. First
close fences new work. Existing accepted GitHub and Keycloak operations drain
before client close; the Actions JWKS provider keeps its cache-owned refresh
drain. Only normal completion of the retained task is successful cleanup.
Failure or cancellation stays observable on every later close of that instance;
neither admission nor a new client is created to conceal the result.

A waiter may cancel without cancelling the shared closer. A done observer checks
task cancellation before retrieving its exception without rendering it. Keycloak
has an additional outer cleanup task: that task needs the same observer even
though its inner HTTP task is observed. Observation does not change retry or
control flow and does not log exception messages, arguments, causes or locals.

## Protected Behavior

RuntimeResources and Keycloak's outer cleanup keep their existing generic retry.
A failed earlier component may leave another component unattempted, and an
independently retry-capable resource can still recover. A consumed HTTP client's
failure is not such a resource. The runtime must not mark it closed after a
successful no-op retry; unresolved physical cleanup remains with the existing
process shutdown and operator recovery contract.

An active cleanup owner has one nonrenewable deadline. Deadline-exceeded and
cleanup-pending states remain terminal; late inner settlement cannot restart
the runtime, renew its deadline, or initiate later resource closes. This does
not promise immediate cancellation of a shielded third-party closer.

No credential grant, endpoint, cache key or bound, auth/session/review epoch,
schema, process exit policy, readiness algebra or metric changes. The client
result is retained in memory only; no new durable receipt or observer framework.

## Alternatives And Falsifiers

Changing only a closed flag cannot retain failure. Retaining the entire runtime
failure would block useful cleanup of unrelated resources. Recreating clients
or retrying private HTTPX pools cannot prove closure of the original resources.
A kernel lifecycle abstraction or AsyncExitStack would still need the same
outcome law and adds ownership without closing another admitted obligation.

Independent native cases use the real pinned client and a supported transport:
a valid response precedes close; resource A is released before an injected
failure and B remains independently observed. Same-instance repeated close must
retain non-success while the successful control releases both. Public integration
and runtime-composed cases distinguish a helper result from the closed marker.
Event barriers distinguish concurrent callers, cancelled waiters, a cancelled
closer, orphaned inner/outer failure and deadline-late settlement.

Reopen on a changed client retry contract, another HTTP owner, changed cancellation
ownership or a required physical-recovery policy. Source and synthetic transport
faults do not establish an observed socket leak, production recovery, live provider
availability or a native pass. No automatic restart or stronger SLO is introduced.
