# Governance Baseline Implementation Plan

Status: implemented; verification pending

Date: 2026-07-26

Owner: `governance_baseline`

## 1. Objective

Implement one owner-approved durable baseline without adding drift,
enforcement, provider mutation, release, or omission authority.

## 2. Execution Order

1. Add runtime and UI requirements, module ownership, design routing, and
   Proofkit bindings.
2. Add canonical governance-state round-trip support and the baseline domain,
   prepared acceptance, ports, and application orchestration.
3. Add one forward-only Alembic expansion, exact schema capability,
   least-privilege grants, append-only operation receipts, schema attestation,
   repository, and unit of work.
4. Compose browser read and approval services with fresh repository grants.
5. Add bounded GET and POST routes, raw body limit, CSRF admission, and
   generated OpenAPI.
6. Add frontend runtime admission, transport, stale-safe hooks, approval
   control, and responsive accessible states.
7. Add unit, persistence, migration, API, frontend, browser, mutation, and
   architecture falsifiers.
8. Run selective planning, native target gates, exact branch-head quality, and
   provider Full Check on the immutable remote head.

## 3. Acceptance

```text
Accept iff
  every changed proof-like path is bound
  and no selective-plan edge is unknown
  and exact accepted or unchanged replay precedes provider I/O
  and stale observation and predecessor races fail closed
  and accepted baseline, audit, and operation receipt commit atomically
  and unchanged commits only its operation receipt
  and retained state round-trips byte-exactly
  and database capability and runtime ACL attestations pass
  and browser scope, digest, CSRF, and generation falsifiers pass
  and all non-claims remain explicit
```

## 4. Rollback

Before production use, downgrade may remove the new capability only when the
baseline and operation relations are empty and the exact successor declaration
is present.
After any retained baseline exists, downgrade must fail. Application rollback
is admissible only while the current database declaration still provides every
capability required by the rollback artifact.

## 5. Review Boundary

Review baseline ownership, approval order, state identity, transaction
atomicity, migration compatibility, and UI truthfulness. Drift algorithms,
provider writes, policy enforcement, release manifests, and omission decisions
are intentionally outside this change.
