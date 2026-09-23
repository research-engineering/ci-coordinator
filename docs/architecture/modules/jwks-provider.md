# GitHub Actions JWKS Provider Module Specification

Status: module specification

Date: 2026-07-14

## 1. Owned Invariant

For the fixed GitHub Actions issuer, a verifier receives an admitted JWK set
only from one bounded HTTPS response or one unexpired immutable replacement of
such a response. Admission retains only valid public RS256 verification
projections. Unknown additive members do not become verifier input, while an
unsupported, invalid, or private-key entry cannot suppress an independent valid
entry. An unknown key identifier may cause one shared refresh per configured
interval. An expired, failed, cancelled, redirected, oversized, or malformed
response yields typed unavailability and cannot become token trust.

Exact constants are owned by
`docs/specs/ci-coordinator-core/jwks-provider-profile.v1.json`.

## 2. State Algebra

For one provider instance, let `K` be an immutable `ActionsOidcJwkSet`, `t` a
monotonic time, and `kid` a header-admitted key identifier.

```text
Snapshot = (K, obtainedAt) | absent
Refresh = inFlight | absent
usableUntil = obtainedAt + maximumCacheAge

get(kid) -> KeySet(K) | Unavailable(kind)
```

The transition relation is:

```text
FreshMatch:
  t < usableUntil and kid in K => KeySet(K)

RefreshRequired:
  Snapshot = absent or t >= usableUntil or kid not in K

SingleFlight:
  Refresh = inFlight => all callers await that exact refresh task

RefreshSuccess:
  bounded admitted response K' => Snapshot := replacement(K')

RefreshFailure:
  no usable matching snapshot => Unavailable(kind)
```

`refreshAllowedAt` advances after both success and failure. Therefore random
unknown `kid` values cannot turn into one provider request per token.

## 3. Transport Contract

The infrastructure adapter owns one fixed HTTPS `GET` to the profile-owned
JWKS URI. It creates and closes its own credential-free, environment-isolated
HTTP client, follows no redirect, applies the exact timeout, and stops reading
before materializing more than the byte bound. The provider rechecks response
status, content type, JSON object shape, `keys` array, and public-RSA JWK
admission before caching anything.

Environment isolation means `trust_env=False`, not a requirement for direct
network egress. Runtime composition may supply the single canonical
credential-free outbound proxy URL admitted by `runtime_settings`; the fixed
HTTPS endpoint, TLS verification, redirect prohibition, timeout, byte bounds,
and response admission remain unchanged. A custom test transport and the live
proxy are mutually exclusive.

The key array is bounded before entry admission. For each object entry, the
provider retains only `alg`, `e`, optional `key_ops`, `kid`, `kty`, `n`, and
`use`. Unknown members such as `x5c` and `x5t` are ignored rather than copied.
Every non-object entry, entry with private RSA members, or unsupported or
invalid RS256 verification projection is discarded independently. An admitted
RSA modulus is at least 2048 bits, as required by the RS256 algorithm profile.
At least one valid key must remain, and duplicate admitted key identifiers
reject the complete response.

The token never supplies a URL, a host, a redirect target, a key set, or a
cache-control value. `identity_admission` owns header and signature admission;
the provider owns neither claim validation nor authorization.

## 4. Safety Proof

Let `Trusted(token)` be the result of the existing RS256 verifier and
`ProviderKeySet(token)` a successful provider result.

```text
Trusted(token)
  => ProviderKeySet(token)
  and HeaderAdmitted(token)
  and SignatureValid(token, selectedKey)
  and ClaimsAdmitted(token)
```

The provider can produce `ProviderKeySet` only by `FreshMatch` or
`RefreshSuccess`. `FreshMatch` requires a prior admitted response and strict
pre-expiry time. `RefreshSuccess` requires the fixed transport, a non-empty
unique admitted verification projection, and immutable replacement. Unknown
members cannot affect verification because they are absent from that
projection. Invalid entries cannot establish trust because they are absent
from the admitted set. All other states return `Unavailable`, which the caller
must map to rejection or FullCI fallback. Thus a stale, ambiguous, malformed,
or attacker-directed provider response cannot establish `Trusted(token)`.

## 5. Concurrency And Cancellation

The provider stores at most one refresh task. Callers await it through a shield,
so cancellation of one waiter cannot cancel the shared request. A cancelled
transport result is typed failure, is never cached, and is backoff-throttled.
Replacement is whole-snapshot only: no request observes a partially merged or
mutated key set.

Close is a terminal lifecycle transition. It marks the provider closed before
cancelling and draining any shared refresh, clears the cached snapshot, and
rejects all later `get` and `probe` work with typed unavailability. A refresh
that completes after close cannot publish a replacement. Repeated close calls
are idempotent.

```text
Closed(provider)
=> no cached key is returned
and no new transport work starts
and no in-flight refresh can publish
```

## 6. File Ownership

```text
identity_admission/oidc_header.py      pure protected-header admission
identity_admission/jwks_contracts.py   provider port, bounded immutable facts
identity_admission/jwks_provider.py    cache state machine and JWK decoding
integrations/oidc_jwks.py              fixed HTTPX transport only
```

No file may combine HTTP I/O, JWT claim admission, signature verification, and
cache transition logic.

## 7. Required Falsifiers

- a cached matching key avoids a second fetch before expiry;
- rotation replaces the whole snapshot and admits a newly present key;
- repeated random unknown key identifiers issue no more than one refresh per
  minimum interval;
- an expired snapshot is never returned after timeout, cancellation, or
  malformed refresh;
- concurrent refresh demand performs one transport call;
- redirect, non-200, non-JSON, malformed JSON, non-JWK keys, and oversized
  bodies do not enter the cache;
- provider-shaped `x5c` and `x5t` metadata does not suppress or enter a valid
  RS256 verification projection;
- non-object, unsupported, undersized-RSA, and private-key entries are
  discarded independently, while zero admitted keys and duplicate admitted key
  ids reject the response;
- exactly 64 raw entries are admitted when otherwise valid, while 65 are
  rejected before per-entry filtering;
- cancellation of one waiter does not cancel another caller's shared refresh.
- close drains an in-flight refresh, rejects later work, publishes no late
  snapshot, and remains idempotent.

## 8. Non-Claims

This module does not expose HTTP routes, authenticate a JWT by itself, make a
plan decision, persist keys, approve dynamic omission, or prove GitHub provider
availability. Ignoring an unknown member does not trust or preserve that
member.
