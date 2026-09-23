# Execution And Contract Integrity Hardening Implementation Plan

Status: accepted for implementation

Design authority:
[Execution And Contract Integrity Hardening](execution-and-contract-integrity-hardening.md)

Frozen baseline: `e7eb73fea9e4ceb5a950f9028240fbdc4b93e064`

## 1. Completion Rule

```text
Complete iff
  every design invariant has one semantic owner
  and every admitted counterexample has a native falsifier
  and every disputed finding has a recorded total disposition
  and all requirements and Proofkit routes close
  and static local gates pass
  and exact-head provider Full Check passes
  and independent frozen-diff review has no unresolved P0-P2
```

No local test result may be represented as provider or production evidence.

## 2. Ordered Slices

### Slice A: Freeze contract and proof topology

1. Add this design and plan to the documentation graph.
2. Strengthen current owner specifications and requirements without creating a
   duplicate semantic authority.
3. Bind every changed source and witness to exact Proofkit routes.
4. Remove routes for retired source paths and reject every unowned path.

### Slice B: Bind execution identity

1. Add required `executionSha` to plan request v2 and authenticated run
   identity.
2. Require and verify the GitHub OIDC `sha` claim.
3. Bind source head and execution revision independently in issuance.
4. Emit `github.sha` from the caller workflow, validate it in the target, and
   use an exact ref for every selected and FullCI checkout.
5. Add push, pull-request synthetic-merge, merge-group, mismatch, and
   missing-claim falsifiers.

### Slice C: Close persistence proofs

1. Add a two-way config-registration/audit pair attestor to migration
   admission. Keep the runtime path constant-cardinality: construct new pairs
   atomically and verify only the exact retained pair on replay. Require an
   equivalent exclusive fence before any future deep-integrity invocation.
   Scope streaming to the attestor `SELECT`, and prove that subsequent DDL and
   DML on the same migration connection retain their ordinary execution mode.
2. Move live and offline PostgreSQL relation kinds to one owner.
3. Add integration counterexamples for orphan events, mismatched pair fields,
   and every admitted relation kind.
4. Under the exclusive compatibility fence, reject ACL replacement while any
   other session for the shared runtime role is live.
5. Document the deployment-owned no-restart precondition and
   generation-specific-role alternative.

### Slice D: Close control-plane HTTP and session contracts

1. Derive a session-handle HMAC key from the current master key.
2. Add cross-key revocation evidence while preserving same-key restoration.
3. Define separate shared no-queue config mutation and query lanes.
4. Apply `no-store` to every config response and complete OpenAPI status and
   export-header projections.
5. Falsify saturation, timeout, authentication, success, error, status, source,
   and not-found paths.

### Slice E: Make capacity and quality oracles honest

1. Withhold GitHub runner capacity until exact class-label eligibility is
   configured and observed.
2. Retain Coverage.py's aggregate combined score floor of `84`, describe it as
   a coarse regression signal, and keep risk-owned line and branch floors plus
   mutation witnesses as semantic adequacy owners.
3. Keep the direct mutation timeout in `proofkit/quality-plan.v1.json`, require
   the provider workflow to equal its 258-minute projection, and synchronize
   stale 250-minute prose and requirement text.
4. Inject monotonic samples into mutation execution and test elapsed arithmetic
   and invalid ordering behaviourally.
5. Use a monotonic clock for container health polling and add deadline-boundary
   and regressed-sample falsifiers.

### Slice F: Resolve migration and production assurance findings

1. Record why the reported forward-migration premise is false under PA-6 and
   the owner-admitted pre-release migration squash.
2. Require reset rather than upgrade for every database created from an older
   unsupported pre-release graph.
3. Freeze migration immutability when the first product release establishes
   the support baseline; do not create a compatibility hop before then.
4. Replace the acronym-based FAPI rejection with a current
   `NOT_DETERMINED` FastAPI Production conformance status.
5. Record exact framework-profile identity and route complete project context,
   conformance evaluation, and external receipts to production qualification.
6. Preserve explicit non-claims for capacity, distributed OAuth abuse control,
   all-replica credential rotation, AnyIO serialization, and edge policy.
7. Contract the runtime profile to GIL-enabled CPython 3.13.15 and project it
   exactly through packaging, locks, containers, `mise`, CI, runtime admission,
   current specifications, and witnesses.
8. Remove the redundant compatibility job after the primary jobs use the sole
   supported runtime.

### Slice G: Close and publish

1. Run JSON, documentation, import-boundary, architecture, lint, format,
   typecheck, OpenAPI, package, requirements, Proofkit, and selective-plan
   static gates locally.
2. Refresh Codebase Memory and verify changed-source coverage.
3. Ask one independent maximum-reasoning reviewer to falsify the frozen diff.
4. Publish one product-named branch and one PR with explicit business deltas,
   rejected findings, and non-claims.
5. Use GitHub Actions for behavioural, PostgreSQL, mutation, frontend, and
   exact-head Full Check evidence.
6. Resolve confirmed findings, obtain one control review, then squash-merge.

## 3. File-Level Ownership

| Owner                              | Expected files                                                                                                          |
|------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Execution identity                 | `identity_admission/actions_oidc.py`, `plan_issuance/*`, trusted-plan workflow, target validators, and fixtures         |
| Registration evidence              | deep config-registration attestor, atomic runtime append/replay checks, migration admission, and integration falsifiers |
| Principal relation and ACL cutover | principal attestors, database-access specification, deploy procedure, and integration falsifiers                        |
| Session revocation                 | `control_plane_identity/crypto.py` and identity falsifiers                                                              |
| Config HTTP projection             | config routers/contracts, app composition, admission, OpenAPI, and route tests                                          |
| Capacity honesty                   | capacity application contract, runtime composition, GitHub specification, and focused tests                             |
| Quality oracles                    | coverage configuration, mutation machine plan, workflow projection, timing runners, and focused tests                   |
| Migration support epoch            | system axioms, persistence specification, deployment procedure, and active requirements                                 |
| Production assurance               | FastAPI Production conformance specification, production readiness route, and roadmap                                   |

## 4. Review Questions

1. Can ambient GitHub context select a checkout without signed payload and OIDC
   identity agreement?
2. Does every owner event and registration resolve exactly once in both
   directions?
3. Are live and offline privilege proofs quantified over one relation domain?
4. Does key rotation revoke only its owned authority?
5. Can a config request wait in an unbounded local queue or return a cacheable
   epoch-dependent result?
6. Can availability be mistaken for workload eligibility?
7. Can ACL mutation overlap another session that uses the shared role, or can a
   drained predecessor restart during deployment cutover?
8. Does each numerical quality predicate have one owner and only checked
   projections?
9. Does every process duration use one monotonic clock domain?
10. Does migration compatibility follow the owner-admitted support epoch rather
    than historical commits?
11. Is every FastAPI Production or production-readiness result backed by its
    complete exact evidence?
12. Does every executable and dependency surface reject a runtime other than
    GIL-enabled CPython 3.13.15?
13. Did any repair add an abstraction without a second behaviour, trust,
    lifecycle, or ownership reason?

## 5. Rollback

Before the first product release, code rollback is an ordinary squash-revert
and an older development database is recreated rather than migrated. Plan
request v1 and v2 are not accepted concurrently: target workflow and
coordinator update as one pre-production contract unit. Shared-role ACL rollback
requires a drained runtime and prevention of successor restart until exact
predecessor grants are restored.
