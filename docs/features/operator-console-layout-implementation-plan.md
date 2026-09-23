# Operator Console Layout Implementation Plan

Status: active implementation plan

Owner: `ci-coordinator.operator-ui`

Design: [operator console layout](operator-console-layout.md)

## Ordered Work

1. Extend the active navigation requirement and its routes. Preserve all
   preceding design/plan files; link this bounded layout decision from current
   specification and roadmap indexes.
2. Remove the Organization select overlay in `RepositoryCatalog.tsx`; keep the
   native control and reuse existing form styles without icon padding.
3. Add one optional desktop presentation preference to `WorkbenchPage.tsx`.
   Keep mounted children, route ownership and mobile disclosure unchanged.
   Project compact geometry and visual labels only above the existing mobile
   breakpoint in `workbench.css`.
4. Add focused component and native browser witnesses to the existing app and
   workbench suites. Update the keyboard oracle for the new desktop control;
   preserve the existing authority, draft, retry and navigation witnesses.
5. Run admitted static/type/Proofkit gates and one frozen independent review
   under the repository reviewer policy. Publish for native GitHub proof;
   merge only with exact required gates green. Release and real Swarm browser
   checks are separate obligations, not implications of source review.

## Acceptance

| Predicate                    | Independent falsifier                                                                                                                              |
|------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| Default mode follows context | Portfolio compact; first repository entry expanded without explicit preference.                                                                    |
| Explicit preference wins     | Both toggle directions persist across repository/portfolio navigation.                                                                             |
| Presentation has no effects  | Toggle leaves URL, selected scope, populated drafts, pending request and request count unchanged; retry retains the same operation identity.       |
| Compact links remain usable  | Actual Tab/Shift+Tab reaches every link with its accessible name and current state; Enter activates the focused task; names have hover disclosure. |
| Mobile is independent        | Desktop compact preference survives a mobile round trip; mobile displays labelled links and the existing menu controls.                            |
| Select has no icon collision | No decorative sibling overlay; keyboard changes an admitted organization, requests that exact inventory and displays its result without old rows.  |
| Layout is usable             | Supported desktop/touch/320px journeys have no viewport overflow, adequate targets and no automatic accessibility violations.                      |

No backend, database, provider access, workflow, dependency or economics-budget
change is included. The separate unpublished budget work remains a roadmap
item, not evidence for this UI repair.
