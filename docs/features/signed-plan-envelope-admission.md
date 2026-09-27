# Signed Plan Envelope Admission

Status: implementation decision
Date: 2026-09-27

## Scope And Decision

Strengthen the exported Python typed-envelope helper under the pre-first-release
support reset. Callers must provide expected_key_id independently from the
candidate. No optional argument, self-derived default or old-call shim is retained.
The wire v1, signer, target JavaScript and complete target authorization are unchanged.
The canonical relation belongs to the
[plan-issuance module](../architecture/modules/plan-issuance.md#typed-envelope-verification);
this document owns the rationale and implementation boundaries.

Use the existing kernel canonical Ed25519 verifier, one captured unsigned mapping
and one injected clock sample. Derive target milliseconds from those exact ISO
strings using standard-library numeric-offset parsing and integer UTC arithmetic.
No timezone-class whitelist: ordinary ZoneInfo values are admitted when their
original ISO spelling has a whole-minute offset. Do not rewrite signed strings.

Retain the original Python expiry/order/TTL conjunct as well as target time guards.
Same-ZoneInfo fold arithmetic can therefore reject an envelope whose UTC interval
passes the target; this is a declared scope limit, not a reason to relax the old
predicate. Millisecond comparison is existing target policy, not new leeway.

## Writer Readiness

| Owner | Intended delta | Protected observations | Independent falsifier |
| --- | --- | --- | --- |
| plan_issuance/signer.py verifier | Required trusted kid, canonical signature and target time guards | Signer class/encoding, PEM diagnostics, old exact lifetime, public export | Valid same-key/wrong expected kid, pad-bit alias, future boundary, sub-ms expiry and old-TTL counterexample |
| test_plan_issuance.py | Explicit Node kid/clock; independent signed vectors | Every old assertion and selected/fallback positive | Literal outcomes, independent signature validity and canonical-byte equality before verifier calls |
| Conformance vector | Fixed typed-domain inputs and literal expected outcomes | No implementation-derived expected acceptance or production key | Both language adapters consume same trusted kid/time; malformed signature preserves payload bytes |
| Module/bootstrap contracts | Declare narrowed Python API and scoped relation | JS raw-wire and downstream identity/execution policy | Documented intentional sub-ms/DST differences, not universal parser parity |

No additional test support module is necessary: the vectors share one existing
issuance test owner. Existing delivered-bundle tests remain independent controls.
Root separately owns requirement/routing/risk and generated projections.

## Lowest-Cost Model

The existing conjunction of independent key, signature and temporal operands is
sufficient. Doc-only or moving the helper leaves the countermodels; copying a
decoder or introducing a verifier framework adds an unnecessary second owner.
Reuse kernel cryptography and standard-library datetime/JSON/base64 behavior.
For a whole-minute-offset ISO value, Q is its UTC delta from the epoch divided
with integer floor by one millisecond. Never use floating timestamp rounding.

## Native Acceptance

Fixed synthetic key material signs requirement-owned literal JSON through the
cryptography library, not SignedPlanSigner. Each signed-field change is re-signed
except explicit tampering. Signature spelling changes preserve unsigned bytes.
Independent literal positive/negative expectations are checked in Python and Node;
matching two implementation outputs alone is insufficient.

Cover skew at 299999/300000/300001ms, the existing sub-ms skew cell, expired/equal/
next-ms expiry, same-ms lifetime, exact TTL and preserved stricter Python TTL;
independent kid substitution, re-signed and unre-signed kid changes; padded,
pad-bit, alphabet, length and cryptographic negatives; schema/algorithm/PEM;
aware ZoneInfo and same-zone fold; malformed time and single clock observation.
Preserve selected issuance, idempotency, not-after and FullCI tests.

All behavioral tests and causal mutations require exact-head GitHub evidence.
No native execution or deployment qualification follows from static checks.
A new supported client obligation, target time/key grammar, runtime pin or canonical
byte change reopens this decision.

## Delivery

[Implementation plan](signed-plan-envelope-admission-implementation-plan.md)
owns ordering and verification; it does not duplicate the module contract.
