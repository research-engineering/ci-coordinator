# Governance Comparison Implementation Plan

Status: implemented; import-boundary hardening pending provider CI and merge

Date: 2026-07-26

Owner: `governance_comparison`

## 1. Objective

Implement one authorization-first exact governance comparison without adding
policy classification, persistence, scheduling, provider mutation,
remediation, release, or omission authority.

## 2. Preconditions

```text
Implemented(governance_observation)
and Implemented(governance_baseline)
```

Publication requires the durable-baseline contract to be present in the target
branch. This ordering is a product dependency, not a branch-management rule.

## 3. Execution Order

1. Add runtime and UI requirements, module ownership, architecture routing,
   this plan, and Proofkit bindings.
2. Pass requirement admission and selective routing before production code.
3. Add the pure exact-state comparator and its complete counterexample matrix.
4. Add authorization-first application orchestration with before-and-after
   active-pointer revalidation.
5. Add fresh browser-grant composition and one bounded read-only `no-store`
   HTTP endpoint.
6. Generate and admit the OpenAPI and TypeScript transport projections.
7. Replace the workbench's independently loaded governance reads with the
   composed evidence response and render exact comparison without policy
   language.
8. Run domain, application, HTTP, frontend, browser, architecture, Proofkit,
   and complete repository gates on frozen bytes.
9. Enforce the comparison package's exact inward dependencies and prove that
   every governance context has one explicit import-boundary rule in the
   executable gate.
10. Reject known standard-library dynamic loading authorities and falsify
    `pkgutil`, `pydoc`, `runpy`, `sys.modules`, and reflective built-in import
    bypasses.
11. Derive rule coverage and scanning from one no-follow inventory, require
    rule applicability for every actual Python file, and reject nested source
    symlinks.

## 4. Acceptance

```text
Accept iff
  every changed proof-like path is bound
  and no selective-plan edge is unknown
  and authorization precedes baseline-store and governance-state reader access
  and store failure performs no governance-state reader I/O
  and exact baseline pointer is equal before and after provider observation
  and relation is derived from exact canonical bytes
  and exact rule deltas use complete canonical bytes without semantic pairing
  and absent baseline remains unbaselined
  and every non-success remains distinct from differs
  and comparison-domain imports cannot reach unadmitted first-party contexts
      or forbidden framework, HTTP-client, environment, OS, or SQL capabilities
  and known dynamic loading authorities cannot hide such an edge
  and one no-follow inventory supplies both coverage and scanned source files
  and every Python-bearing regular or namespace governance package has exactly one
      import-boundary rule applicable to every actual Python file under the standalone witness
  and any source-tree symlink fails before rule evaluation
  and frontend admission independently recomputes the complete relation
  and UI text preserves every non-claim
  and no persistence, scheduler, policy, provider-write, or omission authority exists
```

## 5. Rollback

The slice adds no database state or provider mutation. Rollback removes the
comparison endpoint and frontend projection while leaving observation and
baseline APIs unchanged. No migration or data rollback is required.

## 6. Review Boundary

Review exact state identity, byte equality, rule-set semantics, authorization
order, baseline race closure, response binding, request-generation safety, and
UI truthfulness. Drift policy, severity, waivers, continuous polling,
remediation, release evidence, and omission invalidation are intentionally
outside this change. The import-boundary witness is a conservative static
source policy; it does not claim to sandbox the Python runtime.
