# Exact Runner Capacity Evidence Implementation Plan

Status: accepted implementation plan
Last updated: 2026-09-05
Design: [`exact-runner-capacity-evidence.md`](exact-runner-capacity-evidence.md)

## 1. Objective

Replace the unconditional unknown runner snapshot with a bounded GitHub
self-hosted-runner observation while preserving the exact selected validation
set and conservative fallback for every unresolved case.

Success requires:

```text
StaticExactSelector
and CompleteBoundedProviderEvidence
and DisjointObservedPools
  => capacity-derived additional parallelism is bounded by free eligible runners

otherwise => serial selected execution or the existing FullCI fallback
caller cancellation => propagated without an execution plan
```

For profiles `B(C)` sharing capacity class `C`, the executable bound is:

```text
sum(max(0, maxParallel(P) - 1) for P in B(C))
  <= max(0, freeSlots(C) - |B(C)|)
```

The one serial lane per profile preserves coverage and may queue when observed
free capacity is lower than the number of selected profiles.

## 2. Scope

In scope:

- exact static `runs-on` parsing from already bound workflow blobs;
- capacity-class-to-selector projection;
- repository runner and visible organization runner-group reads;
- bounded strict decoding, pagination, timeout, eligibility, and overlap logic;
- runtime wiring, metrics-compatible fallback, requirements, and tests; and
- deployment documentation for the read-only organization permission.

Out of scope:

- provider writes, reservations, a central allocator, or queue prediction;
- GitHub-hosted or larger-runner capacity;
- workflow-restricted groups and dynamic expressions;
- database persistence, public API changes, UI changes, and target-artifact or
  signed-plan schema changes; and
- pilot target workflow mutation or performance claims.

## 3. Dependency-Ordered Changes

### Step 1: Freeze Requirements And Ownership

1. Add `REQ-CI-RUNTIME-039` for exact runner eligibility evidence.
2. Strengthen `REQ-CI-CORE-005` with the selector, overlap, and bounded-observation
   preconditions without changing its coverage-preservation invariant.
3. Correct `REQ-CI-RUNTIME-013`: mutable default-branch workflow inventory is
   an observational legacy path and cannot override the exact adapter authority
   of `REQ-CI-RUNTIME-027`.
4. Register the requirement in architecture traceability and Proofkit routing.

Acceptance: requirement admission and architecture traceability resolve one
owner for syntax, eligibility, provider evidence, and optimization.

### Step 2: Project Static Workflow Selectors

1. Add a bounded immutable static-selector value under `repo_context`.
2. Extend `RevisionWorkflowCapability` with a canonical job-selector
   projection.
3. Parse scalar, sequence, and `group`/`labels` mapping forms.
4. Return no selector for dynamic or unsupported forms without rejecting the
   otherwise valid workflow capability.

Acceptance: exact workflow topology remains admissible while unsupported
capacity syntax can never become optimization authority.

### Step 3: Bind Capacity Classes

1. Add a capacity-owned selector value representing the trust transition from
   parsed workflow syntax to scheduling evidence.
2. Extend `TrustedExecutionInputs` with canonical class selectors.
3. Require every profile sharing a class to project the same selector.
4. Pass these selectors to the request-scoped snapshot provider.
5. Remove the unused repository-id-only `RunnerSnapshotPort` alias.

Acceptance: no target schema is duplicated, and one class cannot silently
represent multiple runner pools.

### Step 4: Acquire GitHub Evidence

1. Add exact organization paths and read operations to the closed installation
   request catalog.
2. Extend the runner client with visible runner-group and group-runner reads.
3. Add strict bounded decoders for runner and group pages and restrict label
   equivalence to the documented ASCII-safe intersection.
4. Implement one total-deadline provider with exact sequential pagination,
   stable totals, unique identities, pre-I/O page admission, bounded aggregate
   admitted bytes, a transport-owned per-response byte bound, and cancellation
   propagation. Attribute `TimeoutError` to that deadline only when the entered
   `asyncio.Timeout` reports `expired()`; propagate nested timeout failures
   raised outside the client-owned transport-outcome algebra.
5. Read every visible group's membership, compute label/group eligibility,
   deduplicate identical runner facts, and omit every overlapping class.

Acceptance: malformed, incomplete, unauthorized, excessive, or ambiguous
provider evidence returns no optimistic capacity.

### Step 5: Wire Runtime And Deployment Contract

1. Replace `UnprovenRunnerSnapshotProvider` in the connected composition root.
2. Keep the explicit unknown provider for isolated test and consumer-lab
   profiles.
3. Declare the GitHub App's read-only organization self-hosted-runner
   permission and document the external upgrade requirement.
4. Preserve existing runtime metrics cardinality; do not introduce runner,
   label, group, repository, or class identifiers as metric labels.

Acceptance: disabled mode and non-GitHub profiles remain unchanged, and a
missing live permission yields conservative execution rather than startup or
planning failure.

### Step 6: Prove The Change

Add tests for:

- every supported and rejected `runs-on` form;
- case-insensitive conjunctive labels and exact group names;
- complete one- and multi-page repository, group, and group-runner inventories;
- repeated ids, conflicting duplicates, unstable totals, pre-I/O page
  exhaustion, excessive bytes, groups, runners, foreign next links, skipped
  pages, owned total timeout, factory-level nested `TimeoutError`, denial,
  ambiguous `404`, cancellation, non-ASCII labels, and malformed JSON;
- repository plus organization pool union;
- restricted, absent, duplicate-name, and overlapping groups/classes;
- exact zero, unknown, stale, and fresh snapshots;
- unchanged manifest membership under every capacity result; and
- runtime composition selecting the GitHub provider.

Mutation witnesses must independently break label conjunction, group access,
busy/status filtering, overlap exclusion, stable totals, next-link identity,
and pre-I/O page admission.

## 4. Verification Route

Local execution is limited to repository-owned static checks:

1. Ruff lint and format check;
2. mypy;
3. import-boundary and module-ownership policy;
4. requirements, architecture traceability, JSON, text, and Proofkit route
   admission; and
5. static selective-plan projection.

Behavioral, property, mutation, integration, coverage, container, and full
checks run only through the exact-head GitHub workflow. A green run proves only
its bound test surface; live GitHub organization permission and pilot target capacity
remain separate external receipts.

## 5. Rollback

Rollback is additive and does not require data migration:

1. restore `UnprovenRunnerSnapshotProvider` in composition;
2. retain the parser projection as non-authorizing evidence or remove it in a
   successor change; and
3. remove the organization permission after no deployed instance consumes it.

Because optimization never changes validation membership, rollback changes
only shard count and parallelism.

## 6. Completion Criteria

The implementation is complete only when:

- every changed semantic owner has a direct falsifier;
- all static gates pass on the frozen branch head;
- the exact-head GitHub Pull Request Gate and CodeQL pass;
- the PR is squash-merged as one `master` commit; and
- live permission, reservation, production capacity, and measured savings are
  reported as non-claims until their own receipts exist.
