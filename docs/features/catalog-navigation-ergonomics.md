# Contextual Catalog Navigation

Status: candidate implementation design

Owner: `ci-coordinator.operator-ui`

## Scope And Authority

This second UI batch refines the Organization selector and catalog page
controls at base `27028a44e1d9dd8398c531f251aff9548366a609`.
The [active requirements](../specs/ci-coordinator-operator-ui/requirements.v1.json)
own native accessibility (005) and bounded catalog navigation (007).
This design owns the rationale; the [implementation plan](catalog-navigation-ergonomics-implementation-plan.md)
owns work ordering and acceptance. Earlier designs and plans are unchanged.
The [console layout design](operator-console-layout.md) supplies context
for removing the old leading building icon, not an instruction to
restore it. No sidebar structure, ordinary palette, backend, database, provider
or API change is included.

## Decision

Keep the labelled native select and its existing value, disabled options,
selection handler, focus styling and keyboard behavior. Within a local wrapper,
replace implicit browser-arrow placement with one existing Lucide ChevronDown:
16px square, vertically centered, inset 12px from inline end. Reserve 38px of
inline-end text padding; the decorative SVG is hidden from accessibility,
non-focusable and pointer-transparent. Long selected labels may ellipsize inside
the bounded control; native option text remains intact. In forced colors hide
the SVG and restore native appearance and ordinary padding.

In that same forced-color mode, the visible brand text explicitly uses
`CanvasText`. The browser can force painted colors without replacing all
computed author values; a contrast analyzer can therefore combine a forced
background with the original light branding text. The native failure images
show readable black-on-white text, not the reported light-on-white rendering.
An explicit system color preserves the user's palette and aligns these views
without disabling contrast checks or opting out of automatic color adjustment.
See [CSS color adjustment](https://www.w3.org/TR/css-color-adjust-1/#forced-colors-mode);
[the analyzer issue](https://github.com/dequelabs/axe-core/issues/3978) is
background context, not proof about every analyzer release.

Place organization navigation below its selector, inside the same toolbar
group, outside the label. Retain named icon buttons and visible page number.
Repository navigation stays below its own table. For each ready catalog:

```text
showNavigation(page, hasNextPage) = (page > 1) or hasNextPage
previousEnabled = page > 1
nextEnabled = hasNextPage
```

The page coordinate and continuation belong to that catalog's current admitted
response/request lifetime. Empty rows, local search, total count, incomplete
status and the other catalog's continuation are not operands. A last or empty
page above page one therefore retains Previous. Single-page catalogs have no
pagination landmark or disabled-only bar. Loading and failed reads retain the
existing labelled states and explicit retry, not stale page controls.

## Choice And Revision Conditions

| Route                                          | Cost and disposition                                                                            | Critical falsifier                                                                     |
|------------------------------------------------|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| Increase select padding only                   | Cheaper CSS, but native arrow inset remains implicit; insufficient for the requested placement. | Browser arrow remains adjacent to border.                                              |
| Local native wrapper and direct guards         | Chosen: two presentation owners, existing icon/CSS, no new state or request mechanism.          | Text/arrow collision, invisible focus, wrong page operand or changed request lifetime. |
| Custom combobox or shared pagination framework | Adds popup/focus state or abstraction with no required new interaction; not justified here.     | Revisit only for an owner-admitted interaction native selection cannot satisfy.        |
| Auto-load remaining catalog pages              | Changes provider cost and bounds; forbidden.                                                    | Any page request without an explicit page action.                                      |

The toolbar keeps its existing responsive columns, with start-aligned fields
and a min-width-zero selector group. No viewport-dependent font sizing or
inline styles are introduced. Review actual supported Chromium geometry,
forced-color controls and screenshots before visual admission; layout source,
component tests and synthetic screenshots do not prove live Swarm rendering,
provider completeness, Firefox/WebKit support or production readiness.
