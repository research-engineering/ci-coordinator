# Browser Credential Isolation

Status: selected design
Date: 2026-09-26

Owners: [identity and attestation](../architecture/modules/control-plane-identity-and-repository-attestation.md),
[HTTP](../architecture/modules/api-http.md), and
[runtime settings](../architecture/modules/runtime-settings.md).
Delivery: [implementation plan](browser-credential-isolation-implementation-plan.md).
The [roadmap](../../ROADMAP.md) remains the sole execution register under CI-054.

## Problem And Decision

A valid encrypted login transaction is not independently bound to the browser
that started it. An eligible hostile HTTPS subdomain can plant a parent-Domain
`__Secure-` cookie carrying its own unused, valid transaction and navigate a
fresh browser to the matching callback. State, nonce and PKCE do not by
themselves reject a valid whole-bundle transfer. This is a conditional trust
boundary, not an assertion of observed deployment compromise.

Use the existing settings, route setters and outer cleanup owner. Production
transaction names are `__Host-ci_coordinator_oidc_transaction` and
`__Host-ci_coordinator_reviewer_transaction`, with Secure, HttpOnly,
SameSite=Lax, Path=/ and no Domain. The
[cookie specification draft](https://httpwg.org/http-extensions/draft-ietf-httpbis-rfc6265bis.html)
defines the browser-enforced host-prefix constraints. Set, read and delete must
agree; no legacy-name reader or broader fallback is admitted.

The reviewer flow already independently binds its initiating session digest,
actor, profile, proposal, scope and pending transaction. Its matching cookie
policy does not establish a second equivalent transplant defect. Keep that
binding, current review V2, unknown-legacy rejection and exact retained replay.

Development HTTP remains explicitly loopback-only, with the existing distinct
`ci_coordinator_dev_*_transaction` names, non-Secure and callback-specific paths.
The session cookie name, encryption/key derivation, nonce, PKCE, issuer, role,
scope, profile digest, lifetime and durable-session contracts do not change.
Reuse native cookie APIs and one cleanup policy per callback route/method.

## Finite Secret Separation

Let B be admitted break-glass bearer text, W webhook secret text, and S the
optional canonical encoded control-plane session-key text. Connected settings
add exactly `B != W and (S absent or B != S)` in both mapping admission and
direct construction. Mapping rejection names the break-glass field; errors
never include values. Preserve existing metrics-secret separation, the three
control-plane-secret pairwise guard, formats and optional identity behavior.

This is an explicitly stricter configuration language, not proof of entropy,
custody, decoded-key equivalence or universal pairwise secret independence.
No role, emergency action, HTTP authentication grammar or provider grant expands.

## Cutover And Protected State

Follow [deployment guidance](../how-to/deploy-container.md): drain auth ingress,
replace every old serving replica, then restore ingress. Interrupted old login
and reviewer transactions must be explicitly restarted. Old names are ignored;
their cookies and unconsumed attempts expire within existing bounds. Do not
add dual-cookie deletion, a new pending store or automatic command retry.
Completed review receipts, exact retained operations, sessions and audit remain.
For forbidden reused values, rotate B before startup; rotating the session key
is unnecessary and would separately invalidate sessions under its existing law.

## Proof And Alternatives

Independent tests bind literal production/development cookie tuples, exact-name
admission and every response cleanup path. Each new secret pair has a rest-valid
negative and an otherwise-identical distinct positive through both constructors.
The connected browser witness uses the actual backend start header and an
unused real provider callback: a hostile Domain response must store/send a
separate Secure control but not the transaction; victim login must fail, then
the SAME callback must succeed in the initiating browser. Invalid crypto or
prefix-string assertions alone cannot qualify that guard.

Rename alone, root path alone and stronger encryption do not close the full
predicate. A pre-session store, multi-cookie cleanup, generic policy object or
universal secret registry adds obligations without an admitted need. Keep the
finite existing-owner change. Reopen on a new cookie consumer, changed provider
form/callback contract, browser prefix behavior or secret representation.

Native qualification is pending until target-bound CI. No all-browser support,
real hostile sibling, live realm, mixed-version deployment protection or measured
security impact follows from source or a controlled browser response. Cookies
are not port-isolated; an untrusted endpoint on the same hostname is outside
this subdomain-injection repair. A client able to forge arbitrary Cookie headers
is likewise outside the browser-enforced guarantee.
