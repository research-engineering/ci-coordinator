# Operator Visual System

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

## Decision And Ownership

Give the operator console the navy, light-surface and compact-table appearance
of the supplied reference screens. Keep the repository-oriented information
architecture and the exact evidence and command contracts. Use the existing
React components, native HTML, Lucide icons and static CSS; bundle Lato locally.

This design owns appearance, loading presentation and their acceptance criteria.
[The implementation plan](operator-visual-system-implementation-plan.md) owns
execution and verification. The executable owners remain
[operator UI requirements](../specs/ci-coordinator-operator-ui/requirements.v1.json),
especially REQ-CI-UI-003, 005, 006 and 016. It extends the display repairs in
[operator evidence presentation](operator-evidence-presentation.md), whose
formatting, clipboard, UTC and command-uncertainty rules remain authoritative.
It does not reopen the historical design or implementation-plan payloads.

## Evidence Boundary

The supplied conversation and four screenshots are candidate analysis and visual
preferences, not repository instructions or proof of a deployed session. The
initial checkout was `58b8d29e0338e727d4d33ab9e28e47814cf34eac` with unrelated
uncommitted developer-environment work. Current master was
`eed5a6ec56fbc35599aa21b434f989f6e2d7c2e1` (compact sidebar, PR #146).
Implementation is isolated and starts above the exact, GitHub-green presentation
repair `85edcd4280dde4a26521b8ecc4f5712d98c2a60c` (PR #147).

The architecture policy epoch is the module ownership profile at that base.
This is a presentation change, with no semantic co-ownership failure alleged.
Direct source, rather than graph counts or screenshots, owns contract findings.
The fast code graph has no recorded frontend source parse gaps; its exclusions
include documentation, scripts and selected tests, so those require direct reads.

The visual contract selects Lato, navy navigation and a cyan accent as local
presentation choices. Former cross-company theme paths and comparisons are
not exported as design authority. Retain the Lato license and attribution;
color choice alone does not establish provenance or an organization standard.

## Validation Of The Conversation

The earlier presentation design records the complete first-critique disposition.
The following ledger adjudicates the proposed visual direction and material
overstatements against current owners and the reference screens.

| Recommendation or claim                                         | Verdict and consequence                                                                                                                                                                                                                                                                                                                           |
|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Navy sidebar, pale canvas, white cards, cyan interaction accent | Adopt as the user's explicit preference, supported by related themes. Aesthetic preference is not a universal defect theorem.                                                                                                                                                                                                                     |
| A single visual system is absent                                | Qualify: tokens and shared components already exist. Improve that system instead of installing a parallel override theme.                                                                                                                                                                                                                         |
| Replace every section with the same card/component              | Reject universal replacement. Use one token vocabulary with two semantic surfaces: framed summaries/forms and open evidence sections. Avoid nested card borders and excessive padding.                                                                                                                                                            |
| Remove the isolated heavy black section rules                   | Adopt; already repaired in PR #147. Preserve that change.                                                                                                                                                                                                                                                                                         |
| Lato is an established related-product choice                   | Adopt with local WOFF2 assets and the OFL license. Declaring a missing font alone is insufficient.                                                                                                                                                                                                                                                |
| Every differing focus/accent color is inconsistent              | Reject. Focus must remain strongly visible on its actual background. Use separate dark-surface and light-surface focus tokens.                                                                                                                                                                                                                    |
| Softer chips and less uppercase                                 | Adopt. Keep textual labels and five existing semantic tones. Use uppercase only for short contextual eyebrows.                                                                                                                                                                                                                                    |
| Green enforcing badge, yellow non-enforcing badge               | Defer. Neither admitted session nor workbench schema supplies the runtime mode. Do not derive it from selected/full-ci plans, CSS, environment names or this screenshot.                                                                                                                                                                          |
| Green replay means the repository is safe                       | Reject. Replay validity is a local evidence property. Governance byte equality remains informational and never compliance-success.                                                                                                                                                                                                                |
| Add progress bars                                               | Adopt for pending requests only. Use indeterminate progress without invented percentages, ETA or completeness.                                                                                                                                                                                                                                    |
| Keep old results while refreshing                               | Preserve existing owner behavior. Initial loading and authority/scope replacement clear evidence. Same-scope Governance refresh already retains observations, form reason and an admitted command receipt while disabling baseline approval; label those previous observations and add compact progress. Do not extend retention to other owners. |
| Skeletons replace text                                          | Qualify. Skeleton geometry is decorative; preserve an accessible, operation-specific loading label.                                                                                                                                                                                                                                               |
| Extend activation Stepper to run registration                   | Defer. Registration is not the two-stage authority/activation protocol. Reuse visual feedback, not an invented sequence or completion predicate.                                                                                                                                                                                                  |
| KPI-style snapshot summary                                      | Adopt the grouped visual treatment only. No new aggregate or population claim is derived from the bounded snapshot.                                                                                                                                                                                                                               |
| Fixed dates and compact hashes                                  | Preserve PR #147: exact decimal IDs, UTC, inspect/copy, long-cell hints and scrollable tables. Ledger revision is treated as an identity by the repository requirement.                                                                                                                                                                           |
| Replace hash disclosure with tooltip-only copy                  | Reject. Full text must stay keyboard-inspectable when clipboard is denied.                                                                                                                                                                                                                                                                        |
| Convert every mobile table to cards                             | Reject without a task-specific benefit. Tables preserve cross-row comparison and header relationships. Retain labelled keyboard scrolling and test 320px reflow.                                                                                                                                                                                  |
| A sticky mobile header is always better                         | Defer. At narrow heights it consumes content space and can obscure focused controls; the existing labelled menu and post-navigation closure remain valid.                                                                                                                                                                                         |
| Hide governance caveats to reduce clutter                       | Reject concealment. Reduce visual competition through hierarchy and consistent spacing while retaining observation and uncertainty labels.                                                                                                                                                                                                        |
| Default Emotion/MUI cannot simply be added to the current shell | Confirm. Its default runtime style injection conflicts with the current CSP. The dialogue correctly identifies nonce integration as an alternative; MUI also documents build-time Pigment CSS. The dialogue does not claim that MUI is universally impossible.                                                                                    |
| Nonce is a possible migration direction                         | Qualify, rather than reject the direction. The dialogue correctly notes the immutable-shell and specification changes. Style elements and inline style attributes remain separate CSP sinks, so admission requires the exact component set and integration, not the nonce idea alone.                                                             |
| Relaxing CSP is another integration option                      | Technically possible, but neither the dialogue's final recommendation nor this design selects it. It weakens the current browser policy for a visual objective that static CSS can satisfy.                                                                                                                                                       |
| Pick MUI 9 or replace Lucide solely to match a neighbor         | Reject as insufficient justification. Neither version parity nor icon-package parity proves better appearance or lower maintenance cost.                                                                                                                                                                                                          |
| CSS delivers 90 percent in one or two days                      | Unverified estimate, not an acceptance criterion. Use actual changed surfaces and rendered evidence.                                                                                                                                                                                                                                              |
| Tests use roles, so the redesign will not break them            | Reject the assurance. Current browser witnesses also inspect classes and geometry. Role-based queries do not prove unchanged layout, CSP, loading or command behavior; exact-source native execution is still required.                                                                                                                           |
| Dark mode is mandatory for consistency                          | Refute; the supplied target is light content with a dark sidebar. Defer a second theme until requested and separately verified.                                                                                                                                                                                                                   |

Primary framework references: [MUI CSP](https://mui.com/material-ui/guides/content-security-policy/)
and [Pigment CSS migration](https://mui.com/material-ui/migration/migrating-to-pigment-css/).
They establish available approaches, not compatibility with this repository.

## Visual Contract

### Tokens And Typography

- Canvas `#f4f6f9`, white surfaces, muted surface `#f7f9fc`, border `#dce1e9`.
- Navy `#0a0a54` for navigation and primary actions; cyan `#43a7de` for the
  brand mark and dark-surface active indicator. Use a darker blue for readable
  icons/text on white, rather than assuming cyan meets text contrast.
- Body text `#202b3c`, secondary text `#56637a`; Lato normal 400 and 700,
  system fallback, tabular numerals for measurements and dates, monospace IDs.
- Shared 8px card and control radii, restrained 1px border and shallow shadow.
  Nested content uses dividers rather than a shadow on every row.
- Keep information dense: 16px section inset, 12px table-cell inset, 44px
  navigation targets, existing compact row actions and clear keyboard focus.
- Filled status chips use the existing positive, warning, negative, information
  and neutral meanings. No color-only outcome and no success styling for unknowns.

### Shell And Navigation

Keep the always-available sidebar and PR #146 state model: compact by default
on the catalog, expanded in repository views, with an in-memory manual override
for the current SPA lifetime. Expanded width remains 224px; compact remains
80px. The supplied expanded catalog appearance is available through the existing
Expand sidebar control. Preserve readable labels on the mobile menu, Escape,
back/forward, query links and focus transfer. Style toggle buttons for navy.
Do not introduce a tooltip/drawer library or a second navigation owner.

### Page Surfaces

| Surface             | Treatment                                                                                                                                                              |
|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Repository catalog  | One framed white card; restrained header and filter strip; native organization select; distinct visibility/state/access chips; inset pagination.                       |
| Overview and audit  | Grouped white summary card, slim tabs and compact open evidence tables. Identity details remain available without widening closed rows.                                |
| Workflow discovery  | Open page heading, framed revision form and summary, clear inline disclosure chevrons, one framed proposal with flat internal stages.                                  |
| Governance          | Framed repository identity/observation group, compact caveat, information-only comparison, one baseline card, separately labelled effective rules.                     |
| Economics           | Same card/header/inset vocabulary for discovery, registered runs, measurements, comparison and budgets; no invented chart, savings percentage or progress denominator. |
| Loading and failure | Shared indeterminate loading presentation; error and empty states remain separate with existing recovery semantics.                                                    |

### Loading And Disclosure

Use one small `LoadingState` presentation primitive at existing loading branches.
It owns only a readable label, an indeterminate progressbar and optional inert
skeleton rows. Initial/replacement loading uses skeletons. Existing same-scope
Governance refresh uses the compact variant above retained content, explicitly
labelled as the previous observation. It owns no request, retained result, timer,
retry or authorization state. Resolved, empty and failed branches remove the
loading presentation. The baseline form, reason and admitted success receipt
remain mounted across same-scope refresh, including automatic refresh after
approval; baseline commands stay disabled until the owner resolves the request. Reduced
motion disables progress movement while preserving the visible loading label.

Native `details` remains the disclosure mechanism. A CSS chevron has a consistent
position and rotates with `open`; keyboard activation and accessible names come
from `summary`. Hash inspection keeps its existing compact action and exact copy.
Do not hide scroll affordances or make content depend on hover.

## Protected Observations And Counterexamples

```text
visualReady = admittedResultReady, never merely requestFinished
progressVisible implies currentRequestPending
pendingRequest does not imply a known percentage
newAuthorityGeneration implies oldEvidenceAndActionsUnavailable
displayedRuntimeMode requires an admitted runtimeMode operand
governanceBytesMatch does not imply compliant or enforcing
exactCopy(value) = original(value)
```

For Governance, currentRequestPending includes settled.refreshing; pending
refresh does not upgrade the retained observation into fresh evidence.
These predicates have independent operands: query state owns loading/settled/refreshing;
response admission owns ready/error; authority revision owns invalidation; the
absent runtime-mode field forbids a mode assertion; existing governance and
clipboard owners retain their meaning. No new derived business predicate is
introduced. Isolated falsifiers are loading-to-error, loading-to-empty, stale
scope/session replacement, clipboard denial, and byte-match informational tone.

Rendered falsifiers include white-on-white sidebar controls, low-contrast active
icons, missing font assets under CSP, clipped focus rings, dense long identifiers,
page overflow versus intentional table scrolling, disclosure markers detached
from their headings, and animation despite reduced-motion preference.

## Minimum Sufficient Trajectory And Revision Conditions

Changing existing CSS plus one stateless loading component and four local font
assets is preferable to either a second override stylesheet or a MUI migration:
one editable token owner remains, existing interaction code remains admitted,
and the exact browser witnesses cover the affected visual boundaries. The cost
is maintaining a small set of native styles and checking font/license provenance.
This is a bounded choice under current requirements, not a global optimum.

Revisit if a required interaction cannot be expressed accessibly with admitted
native controls, the organization admits a shared package with compatible CSP,
the component set materially expands, or rendered witnesses expose a usability
counterexample. Appearance screenshots prove only fixture-backed rendering at
their recorded viewports; they do not prove fresh login, provider operations,
deployed bytes, production capacity or global design superiority.
