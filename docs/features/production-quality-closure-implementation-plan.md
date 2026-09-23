# Production Quality Closure Implementation Plan

Status: implementation complete; provider closeout remains external

Design authority:
[Production Quality Closure](production-quality-closure.md)

Frozen baseline: `62f621a5bbc971bbe0ac1afa42cd89834cc52525`

## 1. Completion Rule

The plan is complete only when every design-ledger row has exactly one terminal
implementation disposition:

```text
Closure(row) in {
  repaired-and-proved,
  adopted-and-proved,
  externally-qualifiable,
  retained-with-owner-nonclaim
}
```

No phase may mark a product capability implemented because a schema, fixture,
or local test exists. Provider, deployment, capacity, and production claims
require their own evidence classes.

## 2. Change Partition

Use four independently reviewable implementation tranches. A later tranche may
depend on an earlier contract, but must not conceal a second business objective.

| Tranche | Objective                                                               | Design rows  |
|---------|-------------------------------------------------------------------------|--------------|
| A       | Bounded private diagnostics and strict structured logging               | 13-20        |
| B       | Exact asset identity and bounded frontend transport                     | 21-25, 27-30 |
| C       | Hierarchical HTTP, provider, readiness, and database admission          | 1-12, 23     |
| D       | External capacity, audit checkpoint, retention, and production evidence | 36-38        |

Rows 26 and 31-35 are owner decisions, not code tranches. Their non-claims and
roadmap placement are verified during closeout.

## 3. Phase 0: Requirements And Route Closure

Before production edits:

1. Add exact runtime requirements for strict diagnostics, hierarchical request
   admission, and capacity evidence, or strengthen an existing requirement only
   where its semantic owner is unchanged.
2. Strengthen the operator-UI requirement for manifest-bound immutable assets,
   timeout admission, and risk-owned test evidence.
3. Update `proofkit/requirement-bindings.json` so every new design, source,
   test, machine profile, and generated projection has one admitted route.
4. Run dependency, admission, selective-plan, requirement, text-policy,
   documentation-graph, import-boundary, and ownership static witnesses.
5. Reject any path whose owner or witness remains ambiguous before code
   mutation.

## 4. Tranche A: Diagnostics And Logging

### A1. Sanitizer contract

Files:

- `backend/src/ci_coordinator/observability/logging.py`
- `backend/tests/unit/observability/test_observability.py`
- `docs/architecture/modules/observability.md`

Work:

1. Introduce immutable sanitizer limits for depth, node count, collection
   width, scalar UTF-8 bytes, key bytes, and final JSON bytes.
2. Reject reserved system metadata from caller events.
3. Track container identities to close cycles without retaining the source
   graph after emission.
4. Replace unsupported values, oversized scalars, and exhausted budgets with
   stable non-secret sentinels.
5. Reject non-finite floats and serialize with `allow_nan=False`.
6. Normalize secret-field names and cover exact credential classes without
   broad substring rules that redact unrelated business fields.
7. Add counterexamples for cycles, deep and wide graphs, huge scalars,
   malicious mappings, spoofed metadata, `NaN`/infinity, Unicode keys, and
   every secret-name class.

### A2. Private error diagnostics

Files:

- `backend/src/ci_coordinator/api/http/errors.py`
- `backend/src/ci_coordinator/api/http/routers/browser_identity.py`
- `backend/src/ci_coordinator/app/candidate_planning.py`
- `backend/src/ci_coordinator/app/capacity_planning.py`
- `backend/src/ci_coordinator/app/dynamic_plan_service.py`
- `backend/src/ci_coordinator/observability/request_observation.py`
- focused unit tests adjacent to those owners

Work:

1. Define one bounded diagnostic port with closed stage and reason values.
2. Preserve cancellation and every existing public response byte.
3. Record only correlation identity, owner stage, fixed reason, and exception
   class; never exception text, arguments, traceback locals, or provider body.
4. Narrow broad catches to owner-admitted failures except at named
   omission-safety boundaries. Those boundaries catch ordinary `Exception`
   because an unknown planning failure cannot authorize selected execution,
   emit the diagnostic, and preserve FullCI or withheld execution; cancellation
   and `BaseException` still propagate.
5. Add a non-recursive instrumentation-failure counter/fallback.

Acceptance:

- public response snapshot is unchanged;
- every injected unexpected exception yields one bounded private diagnostic;
- logging failure never changes the HTTP or planning result; and
- cancellation is never reclassified.

## 5. Tranche B: Frontend Integrity And Evidence

### B1. Canonical asset manifest

Files:

- `scripts/frontend_bundle.py`
- `backend/src/ci_coordinator/api/http/operator_ui.py`
- `backend/src/ci_coordinator/api/http/operator_ui_bundle.py`
- `Dockerfile`
- frontend Vite build configuration if required
- script and backend unit tests

Work:

1. Generate one canonical sorted manifest after the frontend build.
2. Recompute exact SHA-256 and byte count for every regular asset; reject
   symlinks, unsupported media, missing, duplicate, extra, and mutable paths.
3. Prove the exact filesystem path/size inventory, every per-file limit, and
   the aggregate 32 MiB bundle bound before reading any asset payload at both
   build and runtime admission.
4. Pin the root descriptor and every bundle-internal ancestor with
   descriptor-relative `O_NOFOLLOW` traversal through inventory and reads,
   then require a matching final inventory.
5. Package the manifest with the bundle.
6. At composition, read and verify every asset once and construct an immutable
   in-memory serving map.
7. Redirect unversioned members with `no-store` into the exact manifest-digest
   namespace, serve one-year immutable cache only there, serve the shell with
   `no-store`, and do not expose the manifest.
8. Preserve GET representation metadata on HEAD without transferring a body,
   and implement weak and wildcard `If-None-Match` comparison across every
   repeated header field line.
9. Delete regex-based content-addressing claims and falsify the old
   `app-01234567.js` counterexample.

### B2. Bounded frontend transport

Files:

- `frontend/src/api/shared/boundedFetch.ts`
- affected client modules and tests

Work:

1. Admit timeout as finite positive safe integer within the platform maximum.
2. Admit every response bound within one 32 MiB platform maximum.
3. Construct timeout signals inside the common result boundary.
4. Preallocate an exact response buffer when a canonical admitted
   `Content-Length` exists; keep a bounded fallback when it does not.
5. Require an unencoded response body to equal its declared length; route
   encoded responses through the bounded no-length path because transfer and
   decoded lengths are not equivalent, and remove obsolete wire encoding and
   length headers from the materialized decoded response.
6. Preserve timeout as network failure and malformed/cross-scope evidence as
   invalid response.
7. Treat stream cancellation and reader release as best-effort cleanup that
   cannot delay or replace an already classified transport outcome.
8. Do not duplicate workbench scope admission below its existing trust owner.

### B3. Test evidence

Files:

- `frontend/package.json`
- exact lockfiles
- frontend coverage and mutation configuration
- `.github/workflows/python-persistence.yml`
- backend coverage policy script and its tests

Work:

1. Enable V8 coverage for risk-owned frontend logic and define explicit
   per-owner floors.
2. Add a bounded mutation set for schema admission, identity binding, timeout,
   and state-machine kernels.
3. Require a bounded machine-readable Vitest report for every frontend mutant:
   the baseline must prove at least one executed passing test, and a mutant is
   killed only by an admitted assertion failure. Collection, configuration,
   no-test, unhandled-only, missing-report, and contradictory-counter outcomes
   remain invalid even when the process exits nonzero.
4. Read backend coverage JSON and enforce per-owner/changed-critical floors;
   admit it only as a bounded stable regular file with internally consistent
   counters, and retain aggregate coverage only as information.
5. Admit the exact CI/base/head environment used to select changed-critical
   coverage and fall back to the complete critical scope when no range exists.
6. Keep Chromium as the blocking browser support contract. Record
   Firefox/WebKit as an explicit unadopted product expansion.

## 6. Tranche C: Runtime Admission And Capacity Inputs

### C1. Request admission primitives

Files:

- `backend/src/ci_coordinator/api/http/body_limits.py`
- a narrowly owned request-deadline/bulkhead module if cohesion requires it
- route policy declarations and focused middleware tests

Work:

1. Split overload response from deadline response.
2. Add a weighted process-wide retained-body budget and exactly-once release.
3. Reserve exact admitted `Content-Length` or route maximum when absent.
4. Optimize the single-chunk body path without claiming general zero-copy.
5. Add route deadlines for all non-liveness connected-runtime operations;
   disabled diagnostic runtime remains limited to dependency-free health
   projections.
6. Add class bulkheads for session lookup and readiness. Put workflow discovery
   behind one capability-owned shared reader admission used by both direct
   discovery and proposal review; a route-local limit may shed work earlier but
   cannot serve as the resource proof.
7. Prove cancellation, disconnect, response-start, downstream timeout, and
   nested-middleware behaviour with ASGI falsifiers.

### C2. Readiness

Files:

- `backend/src/ci_coordinator/persistence/readiness.py`
- `backend/src/ci_coordinator/runtime/readiness.py`
- `backend/src/ci_coordinator/api/http/routers/observability.py`
- focused tests

Work:

1. Put single-flight lock wait inside the absolute deadline.
2. Coalesce concurrent probes without creating an unbounded waiter queue.
3. Return only generic public readiness state and mark every readiness response
   `no-store` so caches cannot extend either ready or not-ready across a
   dependency-state transition.
4. Preserve exact private dependency dimensions in bounded metrics and
   diagnostics.

### C3. Credential header admission and metrics authentication

Files:

- runtime settings contracts, environment loading, and examples
- HTTP security/dependency and observability router
- Compose/deployment Prometheus configuration and documentation
- settings, router, and deployment contract tests

Work:

1. Restrict the existing operator bearer to visible ASCII so every
   startup-admitted credential is representable by its HTTP boundary without
   changing its bootstrap authority or lifecycle, and compare only fixed-length
   token digests after bounded request admission.
2. Add a distinct bounded, header-safe metrics bearer secret for connected
   runtime and reject reuse of any configured credential secret.
3. Reject missing, duplicate, malformed, or mismatched credentials before
   metric rendering.
4. Keep disabled local metrics available for bootstrap diagnostics.
5. Require production network restriction independently of the bearer.

### C4. Database and provider bulkheads

Files:

- `backend/src/ci_coordinator/persistence/connection.py`
- runtime settings and composition
- capability-owned workflow-discovery reader admission, direct-route edge
  shedding, proposal-review projection, and focused concurrency falsifiers
- focused unit and integration tests

Work:

1. Admit exact pool size, zero implicit overflow, and finite pool wait.
2. Bind those values to one capacity profile and expose no DSN or pool internals.
3. Reserve a finite subset of provider work in the shared discovery reader so
   every current entry point leaves capacity in the shared exchange budget.
4. Keep cross-replica fairness external and explicit.

## 7. Tranche D: External Evidence Contracts

### D1. Capacity profile and receipt

Add one versioned machine profile and a capability-owned offline codec/admission
boundary:

- `backend/src/ci_coordinator/capacity_qualification/profile.py` owns the
  packaged profile and exact metric domain;
- `backend/src/ci_coordinator/capacity_qualification/model.py` owns immutable
  identities, budgets, measurements, and typed outcomes;
- `backend/src/ci_coordinator/capacity_qualification/codec.py` owns strict
  canonical JSON projection and decoding;
- `backend/src/ci_coordinator/capacity_qualification/admission.py` owns
  Ed25519 verification, validity, expected-identity comparison, complete-domain
  assessment, and `not_qualified` outcomes; and
- the package exposes signature payload bytes but owns no private key or
  measurement producer.

Model construction, signature-payload generation, envelope encoding, and
decoding must enforce the same cardinality, text, structural, and byte bounds;
no public value may be valid while being unrepresentable by its own codec.

The exact receipt binds:

- artifact and source identity;
- environment and replica topology;
- process concurrency, body, DB, provider, frontend, audit, and shutdown
  dimensions;
- container CPU/memory/pids limits;
- workload digest and sample cardinality;
- percentile and maximum measurements against hard budgets; and
- observation time, signer identity, validity interval, and exact profile digest.

The verifier must return `not_qualified` when evidence is absent, stale,
foreign, incomplete, or unsigned. The production `deployment` evidence digest
must cover this receipt without changing non-enforcing startup semantics.

### D2. Audit checkpoint and storage lifecycle

Add:

1. `audit_replay.checkpoint` with a canonical chain-tip statement produced only
   from a complete valid paged replay and bound to a positive JSON-safe ledger
   revision, last event identity/hash, schema, artifact, database identity, and
   time;
2. `audit_replay.checkpoint_admission` with a separate external-signature and
   validity contract;
3. `capacity_qualification.audit_storage` with a closed per-copy inventory for
   live data, WAL, backups, quarantine, key bindings, restore evidence, and
   replay temporary storage;
4. a pure archive/compaction prerequisite assessment requiring an
   authenticated checkpoint, a non-empty verified successor chain, an
   inventory digest bound by the exact capacity receipt, and measurements that
   cover the inventory while remaining inside signed budgets; and
5. no archive, compaction, deletion, signing key, or retention capability.

The service must not claim external custody merely because it generated a
checkpoint candidate.

## 8. File And Architecture Controls

For every new module ask and answer:

1. Which single semantic owner changes when this file changes?
2. Why can the responsibility not remain in an existing cohesive owner?
3. Does the dependency direction follow the proved trust transition?
4. Is the interface needed for a real replacement, lifecycle, trust, or test
   boundary rather than because a layer exists?
5. Can the same invariant be stated once and projected mechanically elsewhere?
6. What exact counterexample does each test kill?

Metrics, line count, exports, churn, and fan-out select review candidates only.
They cannot prove god-file status. Run the repository-owned decomposition gate
after every tranche and disposition every candidate under the exact profile.

## 9. Verification Order

After each semantic tranche:

1. Run only owner-admitted local lightweight static checks.
2. Regenerate OpenAPI and frontend projections when their source owner changed.
3. Refresh the code graph and inspect direct/transitive impact against source.
4. Re-run Proofkit planning because semantic edits invalidate prior routes.
5. Publish the exact candidate and run behavioral tests in GitHub.

Final closeout requires, on one frozen commit:

- formatting and lint;
- Python and TypeScript type checks;
- import-boundary, module-ownership, documentation-graph, dependency, and
  generated-contract checks;
- targeted unit/component/integration tests;
- frontend coverage and mutation;
- backend risk-owned coverage and mutation;
- browser and accessibility tests;
- container/runtime smoke and migration tests;
- Proofkit full checked-scope admission;
- repository `Full Check`; and
- one fresh independent frozen-diff review.

## 10. Rollback

Every tranche is rollback-safe only before external consumers adopt its new
observable contract. B1, B3-B8, and B10 are material interface or operations
deltas. Their rollback requires restoring code, generated contracts, deployment
configuration, and clients together; reverting code alone is forbidden.

No database-destructive rollback is introduced. If a later capacity or audit
schema needs persistence, it receives its own forward migration and downgrade
selection under the repository Alembic protocol.
