# Contextual Catalog Navigation Implementation Plan

Status: candidate implementation plan

Owner: `ci-coordinator.operator-ui`

Design: [contextual catalog navigation](catalog-navigation-ergonomics.md)

## Frozen Writer Scope

Base: `27028a44e1d9dd8398c531f251aff9548366a609`.
Branch: `fix/catalog-navigation-ergonomics`.
Primary workflow: implementation; execution: one writer, no delegation.
Mutation: target change; authority: repository-native; origin: repository.
Only catalog UI, its forced-color shell compatibility, specification, witness
sources and documentation routes are admitted. Database migration is a separate
owner's batch. No push, PR,
merge, deployment, local behavioral runner, browser or server is authorized.
New design and plan paths are additions relative to the frozen base; every
pre-existing design/plan tree entry must remain identical.

## Writer Readiness And Minimum Obligations

The current component and inventory hooks supply the sufficient as-is model:
request keys gate settled data; an active selected installation is retained
only while present; replacement resets repository page/search. The intended
delta is presentation only. Direct truth-table and geometry falsifiers suffice;
no separate architecture model or pagination state machine is needed.

| Changed owner                   | Delta and complete operands                                                                           | Protected observations / derived surfaces                                                                                         | Post-batch validator                                                                |
|---------------------------------|-------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| RepositoryCatalog               | Each ready branch guards its own nav with page > 1 OR hasNextPage; org nav moves into selector group. | Loading/error/partial states, active selection, page/search reset, exact request scope and bounds; component and browser sources. | Independent review plus existing GitHub operator UI job.                            |
| workbench.css                   | Native wrapper, inset, centering, text reservation, forced-color fallback, contextual group layout.   | Native focus/keyboard/disabled behavior, labels, responsive widths; production-shell Chromium geometry and images.                | Independent Chromium desktop, Pixel 7 and 320px witnesses.                          |
| UI requirements and route index | Refine 005/007, bind witnesses and new design/plan.                                                   | Single normative owner; earlier design/plan bytes; no readiness promotion.                                                        | Static requirement, route and documentation admission; independent semantic review. |

Academic Engineering obligations applied to this bounded lane: owner/epoch
binding, candidate-set closure, minimum-sufficient analysis, lower-cost route
comparison, semantic accessibility, scoped projection truth, error-contract
preservation, rendered UI proof, viewport integrity, independent predicate
operands and honest closeout. Historical execution conventions: addition-only design/plan history and
local static-only verification. Compiler/host limitations are reported with
the writer handoff; this matrix is not a machine-admitted bundle or closure.

## Ordered Work

1. Freeze owner source, clean branch/base, intake and protected request behavior.
2. Add this design/plan pair and refine the active UI requirement and routes.
3. Use one local native-select wrapper and two direct pagination guards.
   Use the system CanvasText color for visible branding only in forced colors;
   retain ordinary colors, automatic adjustment and the complete contrast gate.
   Leave inventory hooks, response admission and network calls unchanged.
4. Add focused component scenarios and production-shell Chromium sources;
   replace the obsolete no-SVG browser assertion with decorative-chevron and
   geometry oracles. Preserve preceding keyboard/authority/race witnesses.
5. Run only admitted static lint, no-emit type and bounded contract checks.
   Freeze the complete path manifest, verify addition-only history, commit
   locally and return candidate evidence, not self-admitted closure.
6. A separately authorized root binds independent review under `AGENTS.md` to
   the exact candidate and obtains GitHub behavioral/build/browser results
   through `.github/workflows/python-persistence.yml` (operator UI job).
   Publication and later live Swarm inspection require separate authority.

## Guarantee And Falsifier Matrix

| Guarantee                                  | Observable / witness source                                                                                                                                        | Critical falsifier                                                                                       |
|--------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| Single-page bars absent independently      | `repositoryCatalog.test.tsx` four first-page continuation pairs; native single-page image.                                                                         | Org continuation controls repository nav or local search removes valid nav.                              |
| First/middle/last navigation is contextual | Component and `catalogNavigation.spec.ts` 1 -> 2 -> empty 3 -> 2 -> 1 journeys; org nav geometry inside selector group.                                            | Dropping page > 1 removes the empty-last back path; replacing OR with AND loses first/last controls.     |
| No eager traversal                         | Request page sequence and explicit actions, disabled terminal Next.                                                                                                | A continuation triggers automatic page requests or loads beyond current bounds.                          |
| Errors remain errors                       | Deferred reads, rate-limit text/delay, explicit retry to the same page; incomplete notice.                                                                         | Hidden pagination suppresses warnings/retry, stale rows become actionable, or failure advances a page.   |
| Selection remains scoped                   | Component active-selection refresh, removal/reset; existing app lifetime/race tests; native exact-inventory keyboard journey.                                      | Refresh picks another still-present organization or retains an absent installation's rows.               |
| Native control remains operable            | Native tag/value, Tab/Shift+Tab, arrow/Enter, hidden non-focusable SVG and pointer hit test.                                                                       | Decorative SVG intercepts pointer/focus or changes the accessible name.                                  |
| Geometry is deliberate                     | Native chevron center delta <= 0.5px, 12px inset, 16px box, >= 8px text reservation gap; long-label screenshots and overflow/axe checks in all supported profiles. | Double arrow, border/text collision, detached org controls or 320px overflow.                            |
| Forced colors retain the native affordance | Emulated forced colors: native appearance, hidden SVG, visible focus, keyboard selection and screenshot.                                                           | Both arrows visible or neither usable; screenshot review still required for platform-specific rendering. |

These are source obligations until their exact-target runs are admitted.
Screenshot fixtures are not authenticated provider or deployed-state evidence.
No completeness, production readiness, independent-review success or global
optimality claim follows from this plan or a local static pass.
