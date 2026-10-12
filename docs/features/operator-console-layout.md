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

## Catalog Identity Beside Its Action

The capability plan's acceptance discipline requires narrow catalog screens to
retain target identity alongside its action. At base `b92f95d` the catalog
rendered `Open` in a trailing Action column with only that word as its
accessible name, and ellipsized owner and repository names at 320px. Two long
names differing only in their suffix could therefore render identically. On
narrow and touch viewports the table's 760px minimum width placed the action
beyond the initially visible part of its horizontal scroll region. Both follow
from source geometry; neither was observed in a native browser run.

The repository cell now owns the action. It uses the existing fixed-width
identity column presentation; names wrap at any character instead of
ellipsizing, and the button keeps its visible `Open` text while its
accessible name is `Open owner/name`. Disabled catalog-only rows keep the same
identity-bearing name and their restriction title. The change is local to the
catalog row: the shared table component, catalog request lifetimes, disabled
states, `Catalog only` disclosure and sidebar preference algebra are unchanged.

| Alternative                                    | Disposition                                                                  |
|------------------------------------------------|------------------------------------------------------------------------------|
| Accessible name only                           | Fixes assistive naming but leaves the visible action away from its identity. |
| Repeat the name in the Action cell below 901px | Duplicates identity text and adds a breakpoint-only rendering path.          |
| Middle truncation or disclosure on focus       | Hides part of the identity until interaction; needs measurement or state.    |
| Sticky identity column                         | Adds scroll-coupled layering to the shared table without a second consumer.  |
| Action inside the wrapping identity cell       | Selected: one rendering at every width, no new state or shared abstraction.  |

The cost is a narrower repository column on desktop, where long names wrap.
Falsifiers: a viewport where the full name or its action leaves the initially
visible table area, two similar names rendering identical visible text, or an
action name without the exact `owner/name`. Revisit if a second table needs
row actions with identity, which would justify a shared table option.

Main-area inline gutters were already symmetric and witnessed by the visual
system browser test in compact and expanded sidebar states. Named navigation
links keep a fixed icon-to-label gap; the new browser witness also checks
vertical centering and compact icon centering. Neither required a style change.

## Evidence Boundary

Native component tests cover defaults, both explicit choices, navigation
persistence and absence of request effects. Native Chromium tests cover icon
names, keyboard traversal, native selection, viewport bounds, accessible
navigation in both modes and crossing the mobile breakpoint, icon-to-label
spacing, and similar long catalog identities beside their named actions at
desktop, Pixel 7 and 320px widths. Keep screenshot evidence synthetic. Source
and static checks do not establish rendering or deployment success. After exact native CI and release, inspect the real Swarm
login and changed controls separately; do not infer untested browser support.
