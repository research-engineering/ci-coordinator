# Plan Issuance Module Specification

Status: module specification

Date: 2026-07-08

## 1. Owned Invariant

Bootstrap receives a signed plan envelope that is bound to the authenticated
workflow run, or it receives a signed FullCI fallback envelope.

## 2. Public API

```text
parse_plan_request(raw_body) -> PlanRequest | RequestError
issue_signed_plan(request, trusted_identity) -> SignedPlanEnvelope
verify_issuance_idempotency(identity, request_hash) -> ExistingOrNewIssue
```

## 3. Envelope Binding

Signed payloads must bind:

- schema versions.
- key id and algorithm.
- repository identity.
- event, ref, base SHA, head SHA.
- pull request or merge group identity where applicable.
- workflow run id and run attempt.
- OIDC issuer and audience.
- workflow ref and workflow SHA.
- config epoch, input hash, diff hash, graph hash, policy hash, and configured
  validation catalog hash.
- verifier version.
- fallback state and reason.

For a pull-request request, `ref` and `pull_request_number` are one identity,
not independent caller fields:

```text
event = pull_request
=> ref = refs/pull/{pull_request_number}/merge
```

An inconsistent pair is not a fallback candidate; it is an invalid request.

The configured and directly constructed signer TTL must be in `[1, 300]`
seconds, matching the delivered target validator. A selected plan may expire
earlier at its production authority deadline; a fallback plan has no such
mandatory clamp. Reject an incompatible setting or signer constructor input
instead of silently shortening it or widening the target security window.
An idempotent replay returns its persisted envelope without re-signing it.
Reusing a nonempty issuance store created under an older lifetime rule therefore
requires an explicit compatibility or migration receipt; an empty-store claim
must be verified for the exact deployment, not inferred from source history.

## 4. Failure Behavior

```text
MissingContext => signed FullCI fallback
InvalidIdentity => no selected plan
EnforcementDisabled => signed FullCI fallback
IdempotentSameRequestAndMode => return existing envelope
SameRunDifferentRequest => conflict or signed FullCI fallback, never selected
SignerUnavailable => explicit failure if bootstrap cannot fallback locally
```

`mode` is `selected` or `fallback`. A transition from selected issuance to
disabled enforcement uses the fallback idempotency partition, so a retained
selected envelope cannot bypass a current safety disablement.

## 5. Proof Obligations

| Obligation                              | Falsifier                                        |
|-----------------------------------------|--------------------------------------------------|
| Payload mutation invalidates signature. | Modified selected check still verifies.          |
| Expired plan is rejected by bootstrap.  | Old plan starts selected checks.                 |
| Wrong run id is rejected.               | Plan for run A accepted by run B.                |
| Non-fallback plan has proof metadata.   | Selected execution without input or policy hash. |

## 6. Implementation Mapping

Current pure-core files:

```text
ci_coordinator/plan_issuance/request_parser.py
ci_coordinator/plan_issuance/trusted_identity.py
ci_coordinator/plan_issuance/model.py
ci_coordinator/plan_issuance/signer.py
ci_coordinator/plan_issuance/issuer.py
ci_coordinator/plan_issuance/store.py
```

`trusted_identity.py` validates that a request consumes an
`identity_admission` result for the same repository/run/ref. It must not verify
OIDC tokens or webhook signatures; those operations are owned by
`identity_admission`.

Selected issuance consumes the public opaque capability exported by
`production_admission` and the public subject identity exported by
`reconciliation`. This dependency is necessary because a selected signed plan
must bind both authorities. It is one-way: neither context imports
`plan_issuance`, and issuance cannot construct, deserialize, or widen the
capability. FullCI issuance requires neither dependency value.

```text
SelectedPlan(p) => ExactProductionAuthority(p) and ExactReconciliationSubject(p)
NoProductionAuthority => SignedFullCI
```

The enforcing application service must durably register that exact subject
before invoking the issuer. Registration failure monotonically widens to
FullCI. A committed registration followed by issuance failure is safe because
the subject cannot mint an authority or an envelope. Consequently, a shared
cross-aggregate transaction would add coupling but would not strengthen the
selected-plan safety predicate; authority-and-scope persistence remains the
database-owned foreign-key boundary.

FastAPI transport and target-workflow consumption remain outside this pure issuance
slice. Persistence provides a PostgreSQL `IssuanceStore` adapter at the
persistence boundary; it retains canonical envelopes and performs the same-key
comparison, but does not make this module responsible for SQL, transactions,
or route composition.

## 7. Acceptance Tests

- golden signed envelope fixtures under fixed key and clock.
- tamper, expiry, wrong-key, wrong-run, wrong-repo negative cases.
- idempotency and conflict tests.
- selected/fallback consistency tests.
