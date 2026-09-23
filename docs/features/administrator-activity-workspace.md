# Administrator Activity Workspace

Status: implementation design; native and live qualification pending.

## Decision And Scope

[Administrator Activity](administrator-activity-product.md) owns the journal,
authorized read/export contract, privacy and retention. This document owns its
operator journey and browser boundary. The [plan](administrator-activity-workspace-plan.md)
owns delivery order. Existing repository audit evidence remains available and
is not relabelled as a complete log of administrator actions.

Add a global Activity navigation entry. Security events work without selecting
a repository; the server resolves the authenticated principal's issuer. With a
repository selected, a second tab exposes its admitted business actions. An
issuer-wide view cannot be inferred from repository membership. Display the
server-admitted issuer or repository context instead of asking an administrator
to enter identity-provider URLs.

Use one bounded table with date interval, action and optional exact actor
filters, explicit refresh, cursor pagination and JSON page export. No automatic
full-history download, log streaming, editable audit row or new search service
is justified. Native form controls, existing bounded fetch/session observation
and the current sidebar keep the workflow predictable.

## Protected Relations

```text
Display(page, request) => ValidWire(page) and ExactSourceContext(page, request)
                        and MatchesFilters(page, request)
                        and CurrentBrowserAuthority

SecurityJournal != ImmutableBusinessChain
ExportPrepared != DeliveredToBrowser
FilteredPage != AllActions
```

Pages must have descending unique sequence numbers, at most the requested row
limit, allowed action/outcome combinations, exact repository or issuer source,
and timestamps inside the requested UTC interval and observation boundary.
Business references preserve event ID/hash identity; no raw payload is exposed.
The browser rejects extra fields, unsafe counts, stale source responses and
malformed cursors. A schema pass never substitutes for server authorization.

Changing source, scope, authority or applied filters cancels prior reads and
removes their rows. Session challenge uses the existing shared response observer;
no new login hook or automatic export retry is introduced. Export is an explicit
bounded GET followed by a local JSON download. Only an admitted response can be
downloaded; interruption leaves delivery unknown. Object URLs are revoked.

UTC day filters compile to a positive interval of at most 31 days. Security
retention and the filtered-reference integrity class remain visible. Unknown
historical issuer/subject is not reconstructed. No success placeholder hides
unavailable storage, missing authority or unconfigured identity.

## Alternatives And Cost

Appending another table to the repository overview is cheaper in code but hides
global security events and recreates the long-page problem. A global entry with
two related source tabs adds only navigation and one reusable page renderer.
Reconsider separate pages if independent filters or lifecycle needs make the
two-source page harder to use. A general log viewer or new chart library adds
no needed capability here.

The Activity client owns its small error/source algebra and reuses only neutral
bounded transport. Importing economics presentation or policy merely to reuse a
spinner or GET wrapper would create an unrelated capability dependency. Existing
shared loading/state components are sufficient.

The shared filter layout also applies the existing input color, typography and
42px control height to Activity and analytics. This closes the visible mismatch
between browser-default inputs and command buttons without introducing a
second design system or changing field semantics.

The delivery batch also integrates the
[configuration workspace](configuration-operator-workspace.md). Its source and
rollback state remain owned there; Activity projects the corresponding retained
business references. This closes one administrator journey from an explicit
configuration command to its attributable result. Shared navigation, session
observation and native browser qualification are integrated once; neither
capability acquires the other's domain policy.

## Writer Readiness

| Owner                | Intended delta                                          | Protected observations                                                | Whole-chain validator                                        |
|----------------------|---------------------------------------------------------|-----------------------------------------------------------------------|--------------------------------------------------------------|
| Activity API client  | Strict page/filter/context admission and bounded export | Session observation, no credential material, cancellation             | Positive wire fixture plus each independent operand mutation |
| Workbench navigation | Global Activity with optional scoped business tab       | Existing links, selected repository, browser history, compact sidebar | Native navigation, scope replacement and keyboard cases      |
| Activity panel       | Bounded table/filter/page/export interaction            | No automatic mutation or invented completeness, visible retention     | Component/browser scenarios, live administrator login after deployment |

Root exclusively owns these frontend and documentation files; the backend lane
owns Activity runtime/schema integration. Regenerate contracts only after that
lane finishes. Native tests execute in GitHub. Static gates do not establish UI
rendering, actual login, database atomicity or operational readiness.
