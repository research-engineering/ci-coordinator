# Operator Console Layout

Status: implementation design

Owner: `ci-coordinator.operator-ui`

## Decision

Keep the sidebar on the portfolio and repository pages. Above the mobile
breakpoint it has expanded and compact presentations; compact retains every
navigation link as a named icon. Default to compact on the portfolio and
expanded inside a repository. An explicit choice overrides that default for
the current page lifetime, including same-page history navigation. Mobile
keeps its existing labelled menu, independently of the desktop preference.

Remove the decorative building icon from the native Organization select.
Keep its label, native selection, keyboard behavior and current admission.
No custom dropdown, storage setting, routing dependency or global menu is
needed. The [active requirement](../specs/ci-coordinator-operator-ui/overview.md)
owns the normative contract; the [plan](operator-console-layout-implementation-plan.md)
owns execution. The preceding navigation design is unchanged.

## Rationale And Boundaries

Let `v` be the view and `p` an optional explicit compact preference:

```text
compact(v, p) = p, when p is present; otherwise v = repositories
toggle(v, p) -> p' = not compact(v, p)
toggle -> unchanged route, scope, session authority and command identities
mobile presentation -> independent of compact(v, p)
```

The preference is one local boolean, not repository configuration. Keeping the
same component tree preserves bounded drafts and request lifetimes. No new
authority follows from catalog visibility or from expanding a navigation label.

The reported Organization overlap involves an absolutely positioned icon on
a native select. Current deployed Chromium at 1280px did not reproduce the
overlap; the user's exact browser rendering remains unverified. Removing this
decorative overlay eliminates its collision path regardless of native padding
behavior. It does not prove all possible browser layout defects absent.

| Alternative                           | Disposition                                                                   |
|---------------------------------------|-------------------------------------------------------------------------------|
| Increase native select padding        | Retains the reported overlay and dependence on native appearance.             |
| Replace select with a custom combobox | Adds focus, popup, keyboard and selection state without a search requirement. |
| Remove sidebar on the portfolio       | Conflicts with the requested retained navigation shell.                       |
| Fill sidebar with future sections     | Advertises unavailable functions.                                             |
| Store collapse state persistently     | Unnecessary storage and lifecycle handling for the requested interaction.     |

Named icon links retain current-page semantics and visible focus. Desktop
collapse controls have explicit action labels and tooltips. Mobile links keep
their visible text and touch targets. Existing scope/session invalidation,
catalog filtering, failures, disabled states and backend permissions remain
protected. Revisit the preference lifetime only after an explicit cross-visit
personalization requirement; add a custom select only for a proved interaction
need that native selection cannot satisfy.

## Evidence Boundary

Native component tests cover defaults, both explicit choices, navigation
persistence and absence of request effects. Native Chromium tests cover icon
names, keyboard traversal, native selection, viewport bounds, accessible
navigation in both modes and crossing the mobile breakpoint. Keep screenshot
evidence synthetic. Source and static checks do not establish rendering or
deployment success. After exact native CI and release, inspect the real Swarm
login and changed controls separately; do not infer untested browser support.
