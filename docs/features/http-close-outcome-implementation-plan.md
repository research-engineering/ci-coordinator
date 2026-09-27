# Retained HTTP Close Outcome Implementation Plan

Status: implementation and validation plan
Date: 2026-09-27

Design: [retained HTTP close outcomes](http-close-outcome.md).
Normative owner: [runtime composition](../architecture/modules/runtime-composition.md),
with [GitHub](../architecture/modules/github-integration.md),
[identity](../architecture/modules/control-plane-identity-and-repository-attestation.md)
and [JWKS](../architecture/modules/jwks-provider.md).
The [roadmap](../../ROADMAP.md) is the sole execution register under CI-054.

## Ordered Work

1. Rebind pinned client/library source and the current four production owners.
   Preserve the merged repository-grant additions and existing outer cleanup law.
2. Author real-client transport counterexamples before changing lifecycle code.
   Keep valid pre-close responses, independent A/B ownership and generic retry
   controls. Do not mock the HTTP client's close method to prove the composition.
3. Retain GitHub and Keycloak inner tasks on every outcome, and add the local
   Actions JWKS task with immediate fetch fencing. Retrieve terminal failure
   without logging, guarding cancelled tasks before exception retrieval.
4. Observe Keycloak's existing outer task without changing its reset, retry,
   state transitions or cleanup order. Test both real tasks before test-side
   result retrieval when all waiters are cancelled.
5. Refine the current module contracts and retain every pre-existing design/plan
   payload. Root integrates existing requirement routes and derived hashes.
6. Freeze exact changed paths, modes, source comparisons and static results.
   Independent review follows [AGENTS.md](../../AGENTS.md); all behavioral proof
   runs in GitHub under the existing verification placement rule.

## Native Acceptance

| Case | Required observation |
| --- | --- |
| N1 | Real pinned HTTPX close can no-op after a partial failure; the independent direct-transport retry and ordinary-success controls release B |
| N2 | Public GitHub factory retains failure, rejects new bindings and existing-bound sends, and preserves repository scope |
| N3 | Prepared real Keycloak integration retains failure; prepare/browser/token work stays fenced without another request |
| N4 | RuntimeResources never marks an unsuccessful HTTP child closed on repeat; other eligible children close once |
| N5 | Concurrent close callers share one first task and its success or failure |
| N6 | Cancelling all waiters leaves the closer alive; both actual Keycloak tasks have observed terminal failures before test-side retrieval, without secret output |
| N7 | Closer cancellation remains non-success on repeat, distinct from waiter cancellation; cancelled-task observation does not raise a callback error |
| N8 | One owner deadline cannot renew; late closer success/failure cannot mark runtime closed or begin later cleanup |
| N9 | Accepted-send drain, ordinary repeated success, outer pre-HTTP retry, unrelated resource retry and redacted error propagation remain |
| N10 | Actions JWKS public provider and runtime marker satisfy the same lower-client failure law |

Use existing integration/lifecycle/resource test modules, bounded event barriers
and awaited task settlement. Independent resource state, call counts and runtime
markers must change under the corresponding guard deletion. A task flag alone,
TestClient-only witness or callback fake cannot qualify the public chain.

## Handoff Boundary

Local checks are limited to admitted static source, lint, no-emit type and docs
checks. No native execution or physical release is claimed until root admits
exact-candidate GitHub evidence. Root owns requirements, routes, generated files
and publication. No dependency, migration, new public API or process recovery
mechanism is needed.
