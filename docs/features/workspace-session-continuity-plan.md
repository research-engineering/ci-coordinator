# Workspace Session Continuity Plan

Design owner: [workspace session continuity](workspace-session-continuity.md).
Existing requirements: UI005, UI009, UI016, UI017 and UI023. No new backend
capability, authority or dependency is introduced.

## Ordered Implementation

1. Add one finite shared Economics/Activity selection vocabulary. Extend the
   existing route parser/encoder with optional nondefault coordinates, exact
   cardinality, view/scope guards and canonical omission of defaults.
2. Bind active selections to the existing navigation hook. Preserve same-scope
   inactive selections only in memory, reset on scope replacement, and keep
   browser history and normal modified-link behavior. Serialize only the active
   selection through the existing bounded SSO hint.
3. Pass controlled selection callbacks through Workbench and RepositoryWorkspace
   into Economics and Activity. Keep authority-key remounts and cancellation;
   retain same-authority drafts when switching tabs or primary views.
4. Use the shared UTC formatter in ActivityTable without changing raw datetime
   or event ordering. Do not introduce a second timestamp formatter.
5. Expand existing native route/session/component/browser tests. Reuse controlled
   test harnesses only for isolated components; production-shell tests must
   independently prove the real wiring. Cover renewal with an unresolved write,
   scope replacement, late replies, keyboard selection and non-UTC rendering.
6. Update the five existing requirement projections and affected Proofkit routes,
   route hashes, docs index and roadmap. Preserve historical design/plan bytes.
7. Run local static contracts only, obtain the repository's normal independent
   batch review, then native GitHub checks. Publish one cohesive change and
   squash only after exact-head qualification. Deployment is separately bound.

## Owner Readiness

| Owner                 | Intended change                             | Protected observations                                            | Independent check                              |
|-----------------------|---------------------------------------------|-------------------------------------------------------------------|------------------------------------------------|
| Navigation            | Finite active subview coordinates           | Scope guards, canonical URLs, SSO hint bounds, back/forward       | Parameterized route and return-hint falsifiers |
| Feature selection     | Controlled Economics/Activity tab           | ARIA, same-authority drafts, hidden polling, authority-key resets | Component plus production-shell renewal        |
| Activity presentation | Shared UTC human time                       | Raw instant, sequence, filters and privacy                        | Browser context with non-UTC timezone          |
| Derived contracts     | Current requirement/route/index projections | Existing bindings and historical documents                        | Proofkit, docs graph and exact source hashing  |

Each enum, query cardinality, required scope, active view and canonical default
is independently varied. A wrong-view query cannot be used as the only test of
duplicate or unknown-value rejection. Positive default and every valid nondefault
control are mandatory. Return tests retain hostile URLs, unknown fields,
fragments, stale/future hints, blocked storage and loop-budget boundaries.

## Acceptance And Limits

The renewal browser test must first observe the selected nondefault tab, then
expire its authority and complete the real shell's fixed-login return. It must
observe the same tab, fresh read evidence and no automatic mutation. An old
pending response must complete before asserting it cannot replace the new state.
Cross-repository tests must not preserve a foreign draft or pending operation.

No claim of restoring arbitrary filters, scroll position, nested editors,
unsaved drafts or previous authorization is made. No capacity, complete-history,
provider availability or omission claim follows. If the added navigation causes
new stale state or exceeds the existing hint bound, stop and revise this design
instead of widening security policy or adding persistence implicitly.
