# Task-Oriented Operator Navigation

Status: implementation design

Owner: `ci-coordinator.operator-ui`

## Decision

Replace the all-capability workbench feed with an organization catalog and a
repository workspace. The workspace has overview, workflows, economics,
governance and audit views. Related overview evidence uses tabs. This changes
presentation and navigation, not configuration or CI authority.

The active requirement package remains the normative owner:
[operator UI](../specs/ci-coordinator-operator-ui/overview.md).
The [implementation plan](operator-navigation-implementation-plan.md) owns
execution and acceptance. Existing designs retain their original bytes.

## Why This Boundary

An operator chooses a repository before performing a repository-scoped task.
Catalog selection and task execution therefore have different navigation
lifetimes. Rendering every capability together makes unrelated content and
network reads prerequisites for finding the intended task.

```text
Catalog selection -> exact repository scope -> one visible task
Overview -> one of Plans / Runs / Configuration / Overrides
Workflows -> existing discovery, review and activation
Economics -> existing measurement evidence
Governance -> existing observation and baseline approval
Audit -> existing audit evidence
```

For scope `s`, authority revision `a`, view `v` and read model `m`:

```text
Visible(v, s, a) => ExactlyOneActiveTask(v)
Navigation(v1, v2, s, a) => SameBackendAuthority(s, a)
Navigation(v1, v2, s, a) => NoNewMutationAttempt
ScopeOrAuthorityChange => NoRetainedPersonalizedResultFromPriorContext
```

These are design obligations, not proven statements about the implementation.
Native tests must falsify each implication independently.

## URL And Lifetime

Use closed `view` and overview `tab` query values on the existing `/workbench`
page. Keep the admitted installation, repository and item-limit coordinates.
An old valid scoped URL opens overview. An exact repository-review callback
opens workflows; its hints remain untrusted until existing backend replay.
Absent or invalid scope cannot initiate scoped evidence or command requests;
the independently authorized installation/catalog reads remain available.
Unknown view/tab
values fall back to a declared view, never to additional authority.

Navigation links provide canonical same-origin URLs, browser back/forward,
reload and modifier-click behavior. Query state avoids a new backend wildcard,
route authentication surface or routing dependency for this finite view set.
Revisit this choice if independent nested paths or route loaders remove more
complexity than they introduce.

Do not mount unvisited expensive capability views. Retain visited workflow and
governance forms for only the current scope and authority revision so view
navigation cannot discard a pending operation identity or unsaved input.
Inactive views are absent from layout, focus order and accessibility exposure.
Keep catalog search/page state on return. Retention is bounded by the fixed
view set, not an unbounded repository cache; scope and authority changes
invalidate personalized state through existing owners.

Display a provider-admitted repository name from catalog selection only while
its repository and session-authority coordinates still match. Direct links
may show explicit repository/installation IDs until a catalog selection supplies
that label; labels never enter API authority or a trusted URL parameter.

## Interaction And Disclosure

Primary task navigation uses links with current-page semantics. Overview tabs
use the complete tab keyboard model, one selected panel and visible focus.
Mobile navigation retains readable labels, adequate touch targets and narrow
reflow. Update the page heading and focus after task navigation. Raw hashes,
policy source and diagnostics remain deliberate disclosures, not primary
navigation content. The selected evidence table is directly visible instead
of adding another collapsed section behind the tab. Do not invent unavailable
product capabilities.

## Alternatives And Cost

| Alternative                                  | Disposition                                                                                             |
|----------------------------------------------|---------------------------------------------------------------------------------------------------------|
| More accordions in the same feed             | Does not separate task identity or unrelated initial reads.                                             |
| Router plus backend catch-all                | Additional dependency and authenticated route surface without a current need for dynamic path matching. |
| Unmount every inactive form                  | Can destroy retry identity and dirty input after an uncertain write.                                    |
| Keep every capability eagerly mounted        | Preserves state but retains unnecessary initial reads.                                                  |
| Fixed lazy views with bounded form retention | Selected: separate task ergonomics while preserving current command lifetimes.                          |

The cost is explicit URL/view state and a small bounded retained-view lifecycle.
This is a context-bound preference, not a global optimality claim. Reconsider
if measurements show retained memory is material, or a future owner supplies
durable draft/command state independent of component lifetime.

## Proof Boundary

Preserve all evidence tables, scope checks, typed failures, truncation notices,
abort/stale-response guards, session invalidation and backend mutation
admission. A shorter page cannot justify deleting evidence or hiding failure.
Require native component and Chromium desktop/touch/narrow browser witnesses,
screenshots, accessibility checks, production bundle and exact-head CI.
First-visit witnesses must observe capability content and its scoped request,
not merely the shell heading. Hold an actual command response pending while
leaving and returning to its form; revoke authority while the form is hidden,
then release the response and prove that no obsolete receipt returns.

The existing GitHub browser job retains only synthetic-fixture PNG screenshots,
identified by run and attempt, for seven days. Upload follows an executed
browser step even on failure, but not cancellation or a skipped step. A missing
image is not visual evidence. Raw traces, network captures and logs are excluded.
Use the upstream artifact action pinned to an exact commit instead of a custom
transport. This adds bounded evidence storage, not application permissions.
No backend business change, new permissions, provider write, production
qualification, measured CI savings or completed product roadmap is claimed.

## Platform References

- [WAI-ARIA tabs](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/): individual
  tab/panel relationships, keyboard traversal and focusable content panels.
- [React state lifetime](https://react.dev/learn/preserving-and-resetting-state):
  stable tree position preserves state; an owner-bound key resets it.

## Native Completion Diagnostics

Full Check run `34147327414`, attempts 1 and 2, exhausted the unchanged
2,400-second Python coverage budget after the last visible test cases but before
a complete result. Baseline `6af0e14` completed in 2,289.55 seconds. Neither
observation identifies the expensive operation or proves every test passed.

Keep the standard `python -m pytest` entrypoint and load one observation-only
plugin through `-p`. Four flushed monotonic phase markers separate collection,
the test loop and later finalization. The plugin uses pytest's existing terminal
reporter and session stash, without owning streams, timers, threads or cleanup.
An absent reporter or observer write error cannot replace the hook outcome.
Native CLI, capture, debugger and faulthandler lifecycles remain unchanged.
No assertion, selection, database fixture, coverage floor or deadline changes;
the only added arguments select the plugin.

This is preferable to another blind rerun, speculative fixture caching, parallel
execution or a larger ceiling. Independent review rejected a separate traceback
timer: pytest can cancel it after a failure, and an owned output stream adds a
second cleanup path that can mask the test result. Removing both mechanisms is
sufficient for the first phase-localization question. If phase evidence cannot
identify a causal owner, add targeted profiling of that phase, not another blind
rerun. Markers are not stack samples, CPU measurements, test receipts or proof
that all teardown completed. Retire them when the measured repair and native
proof close unless their ongoing diagnostic benefit justifies their cost.
