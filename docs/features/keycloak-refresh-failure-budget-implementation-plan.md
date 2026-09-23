# Keycloak Refresh Failure Budget Implementation Plan

## Contract

The [design](keycloak-refresh-failure-budget.md) owns semantics, alternatives
and non-claims. This plan closes T8 without changing logout or session policy.

## Delivery

1. Add one monotonic failure deadline to each existing provider cache. Sample
   it in the producer; select every new load through the cache-local gate.
   Reuse the failure interval in the background client and preserve the
   separate successful unknown-key policy.
2. Extend native discovery/JWKS tests for the three deadline boundaries,
   positive-cache precedence, failure followed by successful retry, cancelled
   waiters, delayed settlement and close. Keep existing source/age, parser,
   cryptographic and token-admission tests.
3. Bind the successor design and plan to identity requirements and current
   navigation. Preserve pre-existing design and plan bytes.
4. Run permitted static gates and one independent review under `AGENTS.md`.
   Validate findings against the exact candidate; repair only causal defects.
5. Publish one cohesive PR, require exact-head native GitHub tests and all
   mandatory gates, squash merge, then observe the exact merged Full Check.

## Acceptance

All direct and proactive callers share the gate; no new load occurs during a
failed cache's interval. At its boundary a fresh attempt is possible. Current
positive authority remains usable and stale authority remains rejected.
Cancellation and delayed consumers neither renew the interval nor steal a
replacement task. No provider deployment or live exercise is implied.
