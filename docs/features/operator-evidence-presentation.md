# Operator Evidence Presentation

## Decision

Repair presentation defects without changing evidence, authorization, command
identity, or repository navigation. Use existing React components, native
controls, Lucide icons and shared CSS tokens; introduce no dependency or new
backend capability. Requirements remain owned by
[operator workbench](../specs/ci-coordinator-operator-ui/requirements.v1.json).
Execution and witnesses belong to the
[implementation plan](operator-evidence-presentation-implementation-plan.md).

## Evidence And Disposition

The external review inspected a fixture-backed build of `58b8d29`, not an
authenticated live deployment. Source inspection can confirm presentation
mechanisms; visual adequacy still needs native browser evidence and a live
deployment check. The following ledger covers the supplied critique, including
preferences that are not defects.

| Concern                                                          | Disposition and bounded response                                                                                                                                                                                                      |
|------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Grouped run/job IDs and ledger revisions                         | Confirmed: display exact decimal identity; retain grouping for measured quantities.                                                                                                                                                   |
| Governance uses local dates while overview uses UTC              | Confirmed: reuse the existing UTC formatter.                                                                                                                                                                                          |
| UTC-labelled native date inputs                                  | No timezone defect demonstrated: initialization uses ISO UTC and submission appends `Z`; preserve this contract. Native locale formatting is not timezone conversion.                                                                 |
| Fixed 18rem identifier disclosure and unbounded actor text       | Confirmed layout mechanism: compact disclosure, bounded wrapping and explicit table column hints. Preserve keyboard-scrollable comparison tables.                                                                                     |
| Hash disclosure increases row height                             | Intended disclosure, not data loss: retain full inspection and add exact-copy feedback. No inaccessible tooltip-only replacement.                                                                                                     |
| Inconsistent section headings, heavy borders and fine labels     | Improve shared visual rules, sentence case and minimum readable labels without a new section-component hierarchy.                                                                                                                     |
| Container styles differ                                          | Framed controls remain appropriate; page sections use restrained bands. Different semantic surfaces need not be visually identical.                                                                                                   |
| Inter unavailable                                                | Fallback is valid CSS, not a runtime failure. Explicit system typography removes misleading font intent without a network dependency.                                                                                                 |
| Brand, accent and focus differ                                   | Rejected as an intrinsic defect: focus visibility has a separate accessibility purpose. Preserve the blue focus token.                                                                                                                |
| Disclosure marker placement or absence                           | Confirmed: align headings inline with native disclosure markers and restore an affordance where it was removed.                                                                                                                       |
| Discover heading and nested economics actions lack spacing       | Confirmed: reuse subheading and action bands; distinguish list/detail navigation.                                                                                                                                                     |
| Link-button underline                                            | Confirmed: normalize the shared button style.                                                                                                                                                                                         |
| Single-line policy JSON                                          | Display-only formatting; preserve original source for activation and all identity calculations. Non-JSON source remains unchanged.                                                                                                    |
| Singular counts rendered as plurals                              | Confirmed: one small count formatter, not a localization framework.                                                                                                                                                                   |
| Catalog has several names                                        | Use Repositories as the common operator-facing concept; retain precise installation identity in evidence.                                                                                                                             |
| Only Open is clickable                                           | A named native action is valid. Improve unavailable-action explanation; do not add an ambiguous click handler to the entire row.                                                                                                      |
| Contract jargon and no recovery guidance                         | Confirmed wording opportunity: explain the failed operation and next action without weakening rejection.                                                                                                                              |
| Governance caveats and repeated rules                            | Retain observation-only/completeness truth; clearly label effective rules versus retained baseline evidence.                                                                                                                          |
| Registration receives a read-error label                         | Confirmed: operation-specific error wording must not claim that an uncertain write failed or that evidence was absent.                                                                                                                |
| Overview lacks aggregate selected/full/fallback analytics        | Roadmap work, not permission to invent population totals from a bounded snapshot. Keep scope identity inspectable.                                                                                                                    |
| Disabled baseline replacement lacks an explanation               | Add the missing reason prerequisite to the existing form; keep command admission unchanged.                                                                                                                                           |
| Mobile tables, summary, tabs, non-sticky header and closing menu | Improve compact summary and single-line scrollable tabs. Horizontal table scrolling and closing the menu after navigation are intentional. A global card conversion or sticky header is not justified without task-specific evidence. |

## Protected Relations

```text
displayed identifier = decimal representation of admitted identifier
copy(value) = value, not compact(value)
display instant = same instant expressed in UTC
display policy != command policy transformation
UI-only change => same scope, credentials, command body and command identity
unknown write result != confirmed failed write
bounded snapshot counts != repository-wide analytics
```

The first four relations prevent presentation from altering evidence. The
fifth forbids a visual repair from changing business behavior. The last two
prevent false conclusions from missing acknowledgements or partial data.
Clipboard denial must leave the complete value readable and must not report
success. A response from an old identity must not announce success for a new
identity.

Wide identity and prose cells must not collapse neighboring short values or
column headings into one-character lines. Apply arbitrary word breaks only to
the explicit long-content columns; keep headings intact and let the existing
table container scroll. A bounded page width alone does not prove legibility.
Native geometry witnesses must inspect both the long cell and its neighbors.

## Alternatives And Revision Conditions

Existing formatting/CSS plus narrow semantic column hints has lower ownership
and proof cost than a new design framework, custom combobox or responsive
duplicate of every table. This is a context-bound choice, not a global optimum.
Reconsider it if keyboard or mobile witnesses show inaccessible columns, a
real operator task requires a card-specific interaction, or shared styles
cannot preserve clear boundaries. Any such counterexample reopens this
decision; the review ledger is not a waiver.

No analytics totals, CPU savings, production readiness, or global aesthetic
optimality are claimed by this repair.
