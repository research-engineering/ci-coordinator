# Selective Planning Safety Implementation Plan

Status: implemented and locally verified; provider witness pending merge

Date: 2026-07-29

Design authority:
[Selective Planning Safety](selective-planning-safety.md)

Owner requirements: `REQ-CI-CORE-004`, `REQ-CI-CORE-005`

## 1. Objective

Close the confirmed verifier-independence, selected-manifest reachability, and
catalog-hash amplification defects as one independently reviewable safety
slice.

## 2. Execution Order

1. Update verification and runner-capacity owner contracts.
2. Add independent deterministic-plan admission without importing planner
   orchestration.
3. Route `verify` through the new admission result while preserving existing
   FullCI reason identities.
4. Project a full repository test inventory to selected witnesses, rejecting
   malformed input before projection.
5. Compute validation-catalog identity once after reference closure.
6. Add requirement-bound counterexamples that fail on the previous
   implementation.
7. Bind new proof-like paths and critical witnesses in Proofkit.
8. Run targeted tests, lint, type checking, import boundaries, requirements,
   Proofkit admission, selective planning, and the portable repository gate.

## 3. Falsifier Matrix

| Predicate                                               | Smallest counterexample                                                                                                                                              |
|---------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Verification is independent from planner orchestration. | A monkeypatched planner emits the same proof-tampered plan supplied to the verifier.                                                                                 |
| Every omission is current and justified.                | Proof coordinates are rebound but the obligation responsibility surface is impacted.                                                                                 |
| Catalog classification is complete.                     | One catalog obligation is absent from both selected and omitted sets.                                                                                                |
| Selected witness closure is exact.                      | A required witness or reverse edge is missing.                                                                                                                       |
| Full repository manifests support selective execution.  | Tests for selected and unselected known witnesses are supplied together.                                                                                             |
| Manifest input remains fail-closed.                     | An unknown witness, duplicate test id, or missing selected witness is supplied.                                                                                      |
| Catalog hash is computed once over stable identity.     | Repeated reads rehash, or a mutable collection, duck-typed member, or mutable nested policy is admitted and later changes identity without changing the cached hash. |

## 4. Acceptance

```text
Accept iff
  design laws equal implementation predicates
  and old self-replay counterexample is rejected
  and planner-produced selective and FullCI plans are admitted
  and full test inventory projects to exactly selected witness tests
  and malformed inventory is rejected
  and repeated catalog identity reads do not rehash
  and no mutable or duck-typed identity-bearing catalog value is admitted
  and cached catalog identity equals freshly projected identity at every later read
  and no planner, impact-analyzer, omission-builder, or planner-closure import
      exists in deterministic admission
  and Proofkit reports no unknown changed-path edges
  and the final exact branch head passes Full Check
```

## 5. Rollback

The change adds no persistence, provider mutation, schema migration, or public
HTTP contract. Rollback restores the earlier pure functions, but doing so
reopens the three documented counterexamples and is not a safe operational
fallback. Runtime uncertainty continues to route to FullCI.
