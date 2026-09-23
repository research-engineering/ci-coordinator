# Bounded Browser Session Recovery

Status: feature design
Owner: operator UI; server authority remains control-plane identity

## Decision

Repair the transition from an expired application session to a fresh session
without asking an administrator to reload the entire console. Preserve the
existing token-free BFF and server-owned role, expiry, revocation and CSRF
contracts. This changes recovery UX, not session lifetime or authorization.

Implementation order and acceptance are in the
[plan](browser-session-recovery-implementation-plan.md). The canonical client
requirement is `REQ-CI-UI-009`; this design supplies the change rationale.

## Problem And Smallest Sufficient Model

The current client projects expiry to anonymous and leaves feature requests in
an authentication error. A document reload instead passes through the existing
protected page and Keycloak SSO. A longer provider session can therefore repair
the page without a password, but this does not mean the old application session
was still authorized. A separate live pilot also lost repository/view context.

Reuse the existing session, request and navigation owners. The necessary model
has authenticated, checking, anonymous, failure and explicit logout phases;
request generations distinguish old evidence from fresh admission.

```text
authenticated -- expiry/current cookie-API 401 --> invalidate --> checking
checking -- fresh admitted 200 --> authenticated(new generation)
checking -- 401 --> anonymous --> bounded ordinary SSO navigation
checking -- other failure --> explicit failure/retry
authenticated -- repeat challenge with exhausted recheck budget --> challenged
challenged -- explicit check --> checking
any outstanding check -- logout/unmount --> invalidated
```

The initial anonymous page never starts an automatic login. Automatic recovery
requires a previously admitted session and a subsequently confirmed anonymous
session response. A transport challenge is advisory: it cannot grant identity,
classify a 403 as expiry, retry a command or rewrite its outcome.

## Protected Relations

For session check generation `g`, current generation `G`, logout intent `L`,
mounted owner `M`, and request controller `A`:

```text
AdmitSessionResponse => g = G and M and not A.aborted and not L
RecoverFromChallenge => current subscription and same-origin cookie API
                        and no explicit Authorization and status = 401
                        and not an identity endpoint and not aborted
RedirectAutomatically => prior authentication and fresh anonymous response
                         and visible page and persisted loop budget
RestoreRoute => fresh authenticated session and valid one-use local hint
Recovery => zero automatic mutation replay and zero token persistence
```

Invalidate prior authority before checking; old drafts and pending UI state are
discarded under the existing policy. Aborting a write does not prove it failed
on the server. Recovery stores no command and never resubmits it; an operator
must reread durable state before deciding to issue another command.

One identity request may be active. Expiry and visible-page resumption use the
same transition. Concurrent challenges coalesce; one admitted session permits
at most one challenge-triggered recheck until explicit retry or a new session,
so a feature that persistently rejects a valid session cannot cause a loop.
Budget exhaustion never preserves UI authority: a subsequent current challenge
invalidates it, unmounts protected content and requires an explicit session check.
Install response observation in the commit/layout phase before descendant passive
effects can capture their first request. The bounded registration performs no I/O
or layout measurement; transport remains independent of identity policy.
Explicit logout fences old checks immediately, suppresses automatic recovery and retains its own bounded
failure outcome. A failed logout does not prove remote revocation.

A session response whose expiry is already past on the browser clock is an
invalid local projection, not a reason for zero-delay repeated checks or SSO.
The browser clock is a conservative UX input, never server authority. Suspended
timers are supplemented by a visibility/focus expiry check; this is not a new
background polling service or a promise of immediate cross-tab revocation.

## Navigation And Resource Bounds

Use the existing fixed `/api/v1/auth/keycloak/start` endpoint. Keep callback
validation and its fixed `/workbench` destination unchanged. A same-tab
`sessionStorage` record holds only version, attempt time and canonical
`/workbench` scope/view/evidence-tab coordinates. Reject external URLs,
credentials, fragments, unknown query fields, malformed records and old or
future timestamps. The hint is at most 1 KiB, valid for ten minutes and consumed
once after fresh authentication; neither display metadata nor command state is
stored. Local economics subtabs and unsaved drafts are not restored by this
bounded change.

Allow at most one automatic SSO attempt per two minutes per tab; retain the
attempt timestamp after consuming the route. A recent, malformed or
future-dated guard blocks automatic navigation. Reading an invalid hint retains
its guard veto; only explicit sign-in or logout can replace or remove it.
Unavailable storage blocks
automatic navigation but not explicit sign-in. Explicit sign-in may renew the
hint and budget; automatic recovery never turns failures into repeated login.
Clear the hint on explicit logout. Navigation coordinates grant no access and
must pass the normal server checks after return.

Shared transport owns only bounded response observation with request-lifetime
subscription identity. The identity client owns challenge classification; the
hook owns recovery. This avoids putting authentication policy in every feature
client or making a policy-free transport depend on a feature.

## Alternatives And Revision Conditions

| Alternative                                                        | Decision and reason                                                                                                        |
|--------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Keep error and require reload                                      | Rejected: reproduces the observed unusable transition and loses context.                                                   |
| Add a sign-in link only                                            | Retained as fallback, insufficient as the sole recovery for an established session.                                        |
| Increase session TTL or store refresh tokens                       | Rejected: changes security/custody for a navigation problem.                                                               |
| Revalidate then bounded top-level SSO                              | Selected: uses current authorization and ordinary provider flow; costs one bounded read and, when necessary, a navigation. |
| Hidden iframe, new OIDC SDK, global fetch patch or query framework | Not needed by this scoped contract; adds provider/browser or ownership complexity.                                         |

Revisit if the operator requires draft persistence across identities, economics
subtab deep links, immediate cross-tab logout, different callback targets or a
measured need for provider-token refresh. Those are separate authority and UX
decisions, not silent additions here. Any stale-response admission, redirect
loop, secret-bearing hint or automatic command replay falsifies this design.

## Evidence Boundary

Use parameterized pure transport/navigation cases, focused hook state-machine
tests, existing draft/late-write falsifiers and a production-shell browser
journey. All behavioral execution is GitHub-native. Static routes do not prove
runtime behavior; native browser mocks do not prove Keycloak SSO. The final
development rollout must repeat real login, expiry recovery and logout.

Platform references: [React effect lifecycle](https://react.dev/reference/react/useEffect),
[session storage](https://developer.mozilla.org/en-US/docs/Web/API/Window/sessionStorage),
[page visibility](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API).
Storage availability and effect cleanup are explicit inputs, not assumptions
that browser APIs always succeed. No global optimality or production claim is
made by this design.
