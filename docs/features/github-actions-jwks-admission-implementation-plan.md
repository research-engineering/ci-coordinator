# GitHub Actions JWKS Admission Implementation Plan

Status: implemented and locally verified; provider CI and merge pending

Date: 2026-07-29

Owner requirement: `REQ-CI-CORE-017`

Design authority:
[GitHub Actions JWKS Provider](../architecture/modules/jwks-provider.md)

## 1. Objective

Admit the usable RS256 verification projection of a bounded GitHub Actions
JWKS response without making unrelated provider metadata an availability
dependency.

The rejected implementation treated every member of every JWK as verifier
input. GitHub's valid `x5c` certificate-chain metadata is an array, while the
old snapshot admitted arrays only for `key_ops`. Consequently, one additive
metadata member made the complete provider response unavailable.

## 2. Decision

For a bounded raw key array `R`, let `V(k)` be the retained public RSA
verification projection of entry `k`:

```text
V(k) = {alg, e, key_ops?, kid, kty, n, use}
```

Admission is:

```text
AdmittedKeys(R) =
  unique-by-kid({
    V(k)
    | k in R
      and IsObject(k)
      and NoPrivateKeyMembers(k)
      and RsaModulusBits(V(k)) >= 2048
      and ValidRs256VerificationKey(V(k))
  })

Admit(R) iff
  1 <= |R| <= 64
  and |AdmittedKeys(R)| >= 1
  and no duplicate admitted kid exists
```

Unknown members are ignored because they cannot affect the retained
verification projection. A non-object entry, an entry containing private RSA
members, an RSA modulus below 2048 bits, or an unsupported or invalid
verification projection is discarded. A response with no admitted key, or with
duplicate admitted key identifiers, is rejected. Strict JSON, response-byte,
response-shape, and raw-key-count bounds remain unchanged.

## 3. Rejected Alternatives

| Alternative                                                    | Rejection proof                                                                                              |
|----------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| Reject the complete response when any unknown member exists    | Standards-compatible additive metadata becomes a provider-wide outage without changing signature semantics.  |
| Retain every unknown member                                    | Unused external data would cross the trust boundary and enlarge the verifier's semantic and memory surface.  |
| Admit the first duplicate key id                               | Key selection would depend on provider order and would no longer be unique.                                  |
| Accept private RSA members and rely on public-key construction | The verifier has no reason to retain signing material; rejecting that entry is the least-authority behavior. |

## 4. Implementation Slices

### Slice A: exact contract

- update the machine profile with retained members and per-entry disposition;
- update the JWKS and identity module specifications;
- preserve fixed endpoint, byte, redirect, cache, and expiry behavior.

### Slice B: admission

- project only recognized verification members;
- discard unsupported, invalid, or private-key entries independently;
- require the RS256 profile's 2048-bit minimum RSA modulus;
- reject an empty admitted set and duplicate admitted key ids;
- retain immutable admitted mappings only.

### Slice C: falsification and proof routing

- cover GitHub-shaped `x5c` and `x5t` metadata;
- cover a mixed unsupported-plus-valid key set;
- prove unknown metadata is absent from the retained mapping;
- preserve empty, malformed, duplicate, private, and invalid-key rejection;
- prove the exact 64/65 raw-key boundary before per-entry filtering;
- bind the plan and high-risk owner paths to Proofkit and required tuples.

## 5. Acceptance Predicate

```text
Accept iff
  a provider-shaped RS256 key with x5c is admitted
  and unknown metadata is not retained
  and one unsupported entry cannot suppress an independent valid key
  and private-key entries are never retained
  and undersized RSA keys are never retained
  and the raw-key limit accepts 64 and rejects 65 before filtering
  and duplicate admitted kid values reject the response
  and zero admitted keys reject the response
  and fixed transport and cache invariants still pass
  and Proofkit reports no unknown changed-path edge
```

## 6. Non-Claims

- This change does not prove GitHub availability or historical key continuity.
- It does not add issuer discovery, token trust, plan issuance, or deployment
  authority.
- It does not make unknown metadata trusted; unknown members are discarded
  before verifier use.
