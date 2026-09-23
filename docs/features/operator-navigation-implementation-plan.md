# Operator Navigation Implementation Plan

Status: active implementation plan

Owner: `ci-coordinator.operator-ui`

Design: [task-oriented operator navigation](operator-navigation.md)

## Ordered Work

1. Add `REQ-CI-UI-016` and its exact native routes. Preserve preceding design
   and plan files; update the active specification and roadmap indexes.
2. Add the closed view/tab URL model and history adapter. Keep transport scope
   validation at its current owner; prove ordinary, malformed, duplicate,
   callback, modifier-click, reload and back/forward cases.
3. Replace the one-link shell with catalog and repository task navigation.
   Retain one current scope, session authority and visible task. Introduce
   lazy visited-view lifetime only where needed to preserve mutation state.
4. Project overview evidence into tabs and audit into its own view. Reuse the
   current tables and disclosures without dropping a row or failure state.
5. Update shell/component/browser journeys, including abandoned scope,
   personalized-state invalidation, dirty form and exact retry identities.
   Observe real first-visit content and scoped reads. Hold command responses
   pending across navigation and hidden-form logout/expiry before releasing them.
   Retain only native fixture screenshots by run/attempt for seven days.
   Keep direct snapshot-generation falsifiers independent of the workspace's
   scope-keyed unmount: repository, limit, authority and refresh transitions
   must preserve the current result when an abandoned request resolves late.
6. Run local permitted static and Proofkit gates and review one frozen candidate
   using the repository reviewer policy. Publish the candidate to obtain the
   repository-owned GitHub frontend/component/browser, bundle and full required
   checks. Merge only after exact applicable proof closes; deployment is a
   later receipt.

## File Ownership

| Surface                                         | Responsibility                                            |
|-------------------------------------------------|-----------------------------------------------------------|
| `frontend/src/features/workbench/navigation.ts` | Pure closed route and canonical URL projection            |
| `useConsoleNavigation.ts`                       | Browser history and scope-reference lifetime              |
| `WorkbenchPage.tsx`                             | Session, catalog, navigation and workspace composition    |
| `RepositoryWorkspace.tsx`                       | One scoped task and bounded visited-form lifetime         |
| `WorkbenchEvidence.tsx`                         | Selected existing evidence table                          |
| `EvidenceTabs.tsx`                              | Related evidence tab interaction                          |
| `frontend/src/styles/workbench.css`             | Responsive shell/task layout and focus                    |
| `frontend/tests/navigation.test.ts`             | URL and scope predicate controls/falsifiers               |
| `frontend/tests/navigationHook.test.tsx`        | History identity and native link handling                 |
| `frontend/tests/workbenchSnapshot.test.tsx`     | Request-generation isolation independent of page lifetime |
| `frontend/tests/app.test.tsx`                   | Production shell wiring and existing command journeys     |
| `frontend/tests/browser/workbench.spec.ts`      | Real-browser navigation, reflow and accessibility         |
| `.github/workflows/python-persistence.yml`      | Bounded native screenshot retention                       |
| Operator UI requirements and Proofkit routes    | Normative requirement and native traceability             |

This table declares semantic owners, not an arbitrary file-count target.
Combine a proposed helper with its owner if it removes no real complexity.

## Acceptance Matrix

| Predicate              | Required falsifier and control                                                                                                                                                                                                       |
|------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| One visible task       | Each navigation target excludes unrelated headings and controls; selected target is usable.                                                                                                                                          |
| Lazy reads             | Initial catalog and overview do not request unvisited discovery/governance/economics; first visit produces both the exact scoped read and capability content, not just a shell heading.                                              |
| Scope isolation        | Late old-scope data cannot replace the current scope; invalid scope issues no request.                                                                                                                                               |
| History                | Direct URL, reload and back/forward restore exact view, tab and scope.                                                                                                                                                               |
| Mutation continuity    | Keep an actual response pending across departure/return; the request remains live and unique, then retry uses the same operation ID.                                                                                                 |
| Authority invalidation | Logout/expiry while a visited form is hidden aborts its pending request and discards drafts; releasing a late valid response cannot restore its receipt.                                                                             |
| Evidence preservation  | Every table and truncation/failure/identifier disclosure remains reachable in its selected view.                                                                                                                                     |
| Accessibility          | Links and tabs follow native semantics; keyboard, touch, 320px and desktop have visible focus and no incoherent overlap. Closed navigation keeps the same height across task views instead of absorbing their unused vertical space. |
| Visual evidence        | Exact native run/attempt retains only fixture PNG files with seven-day expiry; inspect the rendered views before accepting visual success.                                                                                           |

Do not run behavioral suites locally. Native browser screenshots and layout
checks are required before claiming visual success. Local type, formatting,
route and schema checks remain static evidence only.

## Native Timeout Closeout

Two unchanged-head native coverage attempts exhausted the existing budget.
Add the design's phase-only plugin to the coverage invocation; preserve
standalone pytest commands, the full case selection and all parent/child limits.
Native tests must independently falsify argument or exit-code changes, swallowed
exceptions, wrapper-result replacement and observer output failure affecting
test results. Compare the actual standard CLI with and without the plugin for
success, assertion failure and collection failure. Reuse pytest output/resource
ownership rather than creating a second timer or descriptor lifecycle. Register
these paths under the existing repository-verification requirement. Diagnose
the next native phase observations before any causal repair; no incomplete run
is merge evidence.
