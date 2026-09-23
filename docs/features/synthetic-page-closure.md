# Confirmed Synthetic Page Closure

Status: proposed developer-harness correction.
Execution: [implementation plan](synthetic-page-closure-plan.md).
Owner: `REQ-CI-DEV-016`; `frontend/dev/session.ts`.

## Decision

A foreign document in the synthetic browser context must either close or cause
the owned synthetic session to fail and tear down. A successful close-request
acknowledgment is not evidence that the page is closed.

Preserve the renderer-close attempt. Bound its close-event confirmation to one
second; if the page remains open, request forced browser-side closure with
another one-second confirmation. Subscribe before each request. Only one
closure sequence may own a page, including across repeated navigations.

## Evidence And Model

The hosted trace from Full Check 34782177557 records foreign `data:` navigation,
the existing renderer request and its acknowledgment, followed by no page-close
event until test teardown. The unmodified five-second closure assertion failed.
This is an observed lifecycle gap, not proof of its frequency or of every
internal Chromium cause. The existing workaround was motivated by
[Playwright issue 42366](https://github.com/microsoft/playwright/issues/42366).

```text
Foreign(page) -> one ClosureSequence(page)
RequestAcknowledged(page) does not imply Closed(page)
RendererConfirmed or BrowserConfirmed -> Closed(page)
not RendererConfirmed and not BrowserConfirmed -> FailedSession + Teardown
```

Each confirmation wait is finite. Losing requests remain observed by their
promise handlers; a timed-out request is not assumed cancelled. Context teardown
contains a page that cannot be individually closed. This is not a proof that
an unresponsive browser process or OS will terminate within the same budget.

## Alternatives And Boundaries

Retaining one renderer request preserves the demonstrated gap. Replacing it
with only the browser request reintroduces the previously observed alternate
close-path risk. Repeated polling or indefinite retries are unnecessary: there
are two supported closure mechanisms and an existing owned-context containment
boundary. Increasing the test timeout would not establish eventual closure.

The successful path preserves the main synthetic page, scenario state, network
deny rules and reset semantics. Exhausted closure fails the session rather than
reporting a successful isolated close. Production UI, real cookies, provider
authorization, server routing and deployment configuration are unchanged.

The APIs are the supported
[Page.close](https://playwright.dev/docs/api/class-page#page-close) and
[page close event](https://playwright.dev/docs/api/class-page#page-event-close),
not a new CDP adapter. Revisit for a changed supported browser, API contract,
new independently reproduced close failure or unacceptable measured latency.

## Required Evidence

Keep the real foreign-document, popup and traffic-denial cases. A native fault
probe acknowledges the renderer request without closing the page, then requires
the browser request, a real close event and the surviving main page. Repeated
foreign navigation must not duplicate the closure sequence. A second probe
acknowledges both requests without effect and requires an explicit failed
session plus actual context closure. No local browser test or server is run.
