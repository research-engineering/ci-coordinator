# Configuration Operator Workspace Plan

The sidebar-cancellation witness begins before any mutation, with an unlocked
workspace and an observed in-flight source export. Actual navigation must abort
that request and prevent its late download. The separate uncertain-registration
scenario proves command retention only; an already locked command is not
evidence that sidebar visibility stops reads.

Status: writer implementation delivered; native qualification assigned to root
Date: 2026-09-13
Design: [Configuration workspace](configuration-operator-workspace.md)
Baseline: `3886bd244a651e8810b55e6a1dd4ff749a1fc25e`

## Ordered Batch

1. Read lifecycle owners and retained workspace/session/navigation contracts.
   Freeze the design's minimum model, protected observations and readiness rows.
2. Implement generated-type-bound lifecycle schemas, identity checks, bounded
   transport and exact source upload/export. No backend policy implementation.
3. Add the retained Configuration page, Source and Retained epochs tabs,
   validation diagnostics, registration review, rollback review/confirmation,
   stable uncertain commands and Workflows activation handoff.
4. Integrate only navigation, RepositoryWorkspace, WorkbenchPage and development
   proxy. Preserve archive unknown-operation retention and current activation.
5. Author independent source-bound client, component, proxy and browser witnesses.
   Cover each readiness operand, adverse identity, conflicting revisions,
   stale reads, hidden views, exact replay, keyboard and narrow layouts.
6. Run scoped static formatter/linter and no-emit TypeScript. Inspect the final
   changed paths and commit only this cohesive owned delta. Do not execute native
   tests, providers, browsers, servers, databases, Docker or publish remotely.
7. Return the commit and unresolved qualification predicates to root. Root
   updates governance/generated routes and integrates Activity navigation,
   performs independent review under `AGENTS.md`, then exact-head native CI.

## Witness Oracles

| Boundary      | Positive oracle                                                               | Independent counterexample                                                                                                               |
|---------------|-------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| Validation    | Raw source bytes and backend hash projection agree                            | One source byte, declared format, scope, hash, profile byte bound or request changes                                                     |
| Registration  | Confirmed request contains exactly validated text/format and one operation ID | Edit after validation, omit confirmation, drift response epoch/status, lost response followed by navigation/replay                       |
| Status        | Correct scope, sorted bounded rows, exact cursor alias                        | Duplicate rows, wrong scope, oversized page, wrong cursor, older revision or same revision/different active epoch                        |
| Source export | Raw body, count, type, ETag, epoch and digest match selected summary          | Change each independently; delayed response after selection/navigation/authority change                                                  |
| Rollback      | Selected retained target, active revision, reason and explicit confirmation   | Missing active pointer, same target, stale review, altered reason, revision conflict, wrong success revision, replay command replacement |
| Identity      | Current configure/activate roles and CSRF                                     | Denied role, expiry, actor/CSRF/authority/scope change, 401/403 observation                                                              |
| Navigation    | Same-authority commands survive tabs/sidebar without replay                   | Hidden reads continue; scope change retains foreign source/command                                                                       |
| Browser       | Keyboard reachable controls, responsive layout, accessible names              | Axe violation, overflow, focus loss, provider markup creates HTML                                                                        |

Wire tests must not normalize query aliases or mutate the response before the
target validator. Hash fixtures use Node crypto and the backend projection,
not the production identity helper. UI tests inspect outgoing requests rather
than testing only internal helpers. Browser mocks remain synthetic witnesses,
not provider or deployment proof.

## Completion Split

Writer delivery requires complete user-visible integration, authored witnesses,
static checks and an owned commit. Root acceptance additionally requires fresh
target/evidence binding, capability-profile admission, current generated
contracts/traceability, independent oracle review and native CI. An existing
green suite never proves the missing product predicates.

## Writer Handoff

Steps 1-6 have an implemented delta and authored native witnesses in
`frontend/tests/configuration*.test.*` and
`frontend/tests/browser/configuration.spec.ts`. The fixture uses independent
Node SHA-256 for source/epoch binding and deliberately synthetic opaque compiler
hashes; it is not a backend compilation or policy-admission witness. BOM
preservation is tested at file input, while its validation test expects the
backend-owned rejection, not a fabricated successful admission.

Scoped Biome and `pnpm exec tsc -b --pretty false` are the local static gates.
They do not execute the authored tests. The Node TypeScript file list adds the
new proxy module as part of that projection's build contract.

Step 7 remains root-owned: admit the new `configLifecycle` capability in the
module-ownership profile, bind the new configuration hook/UI witness paths into
coverage and proof selection, update derived contracts and documentation routes,
integrate concurrent Activity navigation, and run independent exact-head review
and native CI. There is no local browser, accessibility, native test, deployment
or provider-success receipt from this writer.

## Browser Qualification Closure

The registration review is a level-two section beneath the page heading; its
visual size remains compact independently of semantic heading level. Keep the
configuration stylesheet in the existing application entrypoint order after
licensed font tokens, rather than injecting it ahead through a component import.
The production font/license and accessibility witnesses remain unchanged.
Keyboard witnesses enumerate the added global Activity and repository
Configuration destinations without removing checks for the existing links.
Fresh native browser qualification must confirm these source changes.
