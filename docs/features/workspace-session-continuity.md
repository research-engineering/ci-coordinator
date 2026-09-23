# Workspace Session Continuity

Status: proposed successor for navigation presentation only.
Owners: operator UI requirements 005, 009, 016, 017 and 023.
Execution: [implementation plan](workspace-session-continuity-plan.md).

## Decision

Preserve the selected Economics and Activity tab through reload, browser
history and the existing bounded SSO return. Extend the current closed URL
vocabulary; do not preserve commands, credentials, responses or drafts across
authority changes. Activity timestamps use the existing shared UTC formatter.

This supersedes only the subtab exclusion in the
[previous recovery design](browser-session-recovery.md). Its authentication,
invalidation, redirect, storage and retry contracts remain unchanged. It extends
[task navigation](operator-navigation.md), not server authorization or routing.

## Observed Problem

On the deployed predecessor, renewal correctly removes old session authority
and resumes through ordinary SSO. Component-local selection then returns to
Registered runs or Access and sessions instead of the user's selected tab.
Separately, Activity uses browser-local time while its filters and the common
UI contract use UTC. A five-minute authority lifetime makes lost position a
repeated usability cost; changing that lifetime would not fix navigation.

## Contract

Let S be an admitted repository scope, V the main view, E an Economics tab,
A an Activity source and G the independent authorization generation.

```text
Navigation = (S?, V, overviewTab?, economicsTab?, activitySource?)
RetainedNavigation excludes (G, token, CSRF, command, response, draft, actor)

economicsTab present => S admitted and V = economics
activitySource = business => S admitted and V = activity
activitySource = security => V = activity

decode(encode(N)) = normalize(N)
encode(decode(encode(N))) = encode(N)
admitSSOReturn(url) => url = encode(decode(url)) and existing same-origin guards

G changes => old responses/commands/drafts invalidated
fresh G and admitted N => fetch under G and display N
restore(N) => no automatic mutation
```

Only finite enum values are admitted. The defaults `registered` and `security`
are omitted from canonical URLs. Existing URLs preserve their meaning. The
parser discards duplicate, unknown and wrong-view selection fields; therefore
strict SSO canonical equality rejects such a noncanonical return rather than
retaining hidden state. Business Activity without a scope falls back to
security and cannot issue a repository read.

The URL owns the currently active subview. Same-page task switches may remember
the previous selected tab in memory for the same scope, but only the active
view's selection is serialized. Changing repository resets that memory.
Back/forward and modifier-click links follow the same admission as reload.
Selection changes must not remount same-authority form owners or reset pending
operation IDs. On authority replacement, those owners still remount and read
fresh data; only the inert tab selection survives.

The one-use return hint remains bounded to 1 KiB and ten minutes, with the
existing two-minute automatic-login cooldown. Fixed server callback targets,
CSRF handling, role admission and logout are unchanged. No new storage key,
backend endpoint, router dependency, arbitrary filter or display label is added.

## Ownership And Alternatives

| Candidate                                       | Decision                                                                          |
|-------------------------------------------------|-----------------------------------------------------------------------------------|
| Keep component-local tabs                       | Cannot preserve the selected tab through full-page SSO.                           |
| Increase session lifetime                       | Alters security and still loses position on reload; rejected.                     |
| Store complete UI state                         | Adds sensitive lifecycle/retention and command-replay risks without need.         |
| New router/state framework                      | More migration and integration surface than this finite vocabulary needs.         |
| Extend existing route and controlled selections | Chosen: reuses canonicalization, browser history and the current return protocol. |

The route owner controls canonical coordinates. Feature components own labels,
ARIA keyboard behavior and their ephemeral read/form state. A small shared
selection vocabulary prevents feature-to-navigation cycles and duplicate enums.
The authorization hook remains independent of feature selection.

The preference is bounded to this finite console. It is not a claim that a
handwritten router is universally preferable. Revisit for independently loaded
nested routes, owner-approved cross-session drafts, sensitive filters or a
demonstrated routing-library simplification.

## Time Presentation

Activity retains its admitted RFC3339 source in the `time` element and projects
the human label with `formatDateTime`, including UTC. The server's interval and
event ordering do not change. A non-UTC browser must render the same label;
local timezone must not silently change the meaning of the adjacent UTC filters.

## Proof And Falsifiers

Parameterize all admitted tab values and independent wrong-view, duplicate,
unknown, missing-scope and return-hint cases. Exercise browser back/forward,
full renewal and late pending writes through the production shell. Assert no
automatic replay, fresh authorized reads, correct ARIA selection and no old
draft restoration. A component-only callback test cannot close shell wiring.

One wrong selected tab after renewal, a cross-scope retained command, an accepted
noncanonical return, an automatic POST, or local-time Activity label refutes the
corresponding claim. Native GitHub qualification is mandatory; live predecessor
observations prove the problem, not correctness of the successor.
