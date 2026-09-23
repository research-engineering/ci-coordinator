# Target Authority Relation Implementation Plan

Status: accepted pre-enforcement plan

Date: 2026-08-31

Design authority:
[Authority Transition Safety](../architecture/cross-cutting/authority-transition-safety.md)

## 1. Objective

Close the registration-completeness gap before production omission can be
authorized. Preserve the existing request hot path and native FullCI fallback.

```text
ProductionOmissionAllowed
  -> IndependentFullRowTargetAuthorityRelation

not IndependentFullRowTargetAuthorityRelation
  -> FullCI or enforcing startup rejection
```

## 2. Scope

This plan implements only the currently reachable target-authority relation.
It does not implement durable copy tracking or a GitHub mutation engine. Those
features require their own accepted plans after their revision predicates
become true.

## 3. Sequence

### Stage A: Relation contract

1. Define one versioned target-authority row algebra and canonical codec.
2. Define the content-addressed non-empty Phase-0 native-authority baseline,
   owner-approved transition-delta algebra, and deterministic expected relation.
3. Require exactly one disposition for every baseline row, attributed
   successors or approved introductions, and explicit authorized retirements.
4. Define a total raw-domain projection in which every independently observed
   workflow and provider candidate maps to exactly one authority or explicit
   owner-approved non-authority row.
5. Bound row count, row bytes, manifest bytes, text, nesting, and producer
   identity.
6. Distinguish `present`, `not_applicable`, and `unknown` without truthy
   coercion.
7. Add mutation vectors for every authority-relevant field.

Exit: changing any admitted row component changes canonical bytes or is
rejected; an empty baseline, unmapped baseline row, unapproved introduction,
agreed omission, or unclassified raw candidate cannot produce an expected
relation.

### Stage B: Independent producers

1. Capture the complete pre-adoption native authority baseline before target
   adaptation, from owner and provider inventories independent of both later
   producers.
2. Build the owner-approved transition delta and expected relation before
   deriving registration rows.
3. Build registration rows from the expected relation, owner-reviewed target
   policy, catalog, registry, and consumer-lab declarations.
4. Build observation rows from exact-revision Git-tree/workflow discovery and
   provider governance evidence without accepting baseline, delta, expected, or
   registration keys as enumeration input.
5. Independently inventory every `.github/workflows` tree entry under the
   versioned domain-separated stable-manifest codec. Reject unknown modes and
   types, symlinks, gitlinks, duplicate paths, incomplete traversal,
   nullable-field mismatches, and size or object-id mismatches. Recursively
   recompute every retained tree object and verify the
   source-commit/root/workflows-subtree object chain. Retain the bounded
   authenticated provider commit binding plus exact tree and regular-blob
   bytes, derive one stable complete-domain manifest digest that excludes
   commit and ancestor identities, and emit a separate source-binding receipt
   that proves the current commit resolves to that stable digest. Do not claim
   raw commit-object reconstruction from GitHub's structured REST response.
6. Derive the raw candidate domain from all regular workflow blobs, parsed
   workflows and jobs, target policy, catalog, registry, and profile members,
   and provider authority members; reject parsing failure or any candidate
   without exactly one classified expected row.
7. Emit producer identity, version, source epoch, subject, and complete
   attributed evidence bytes.
8. Prove both inventories are independently complete relative to the expected
   relation and their declared bounded source domains.

Exit: deleting a key from either producer is detected as missing or extra by
the other producer or expected relation; deleting the same key from both
producers still conflicts with the baseline conservation proof.

### Stage C: Comparator and receipt

1. Verify the baseline, transition delta, non-empty expected relation, raw
   candidate inventory, and total projection.
2. Compare both producer key sets and canonical full-row bytes against the
   expected relation.
3. Return typed missing, extra, mismatch, stale, malformed, unknown,
   incomplete-domain, and unauthorized-transition outcomes.
4. Produce a content-addressed relation receipt only for complete equality,
   exact workflow-manifest digest agreement, and total raw-domain projection.
5. Retain the baseline, delta, expected relation, raw candidate inventory,
   independently produced stable manifests, source-binding provenance receipts,
   and comparison ledger under explicit retention bounds.

Exit: every non-equality outcome produces no affirmative receipt.

### Stage D: Production admission

1. Implement the successor receipt schema and codec, relation persistence and
   admission, runtime verification, and native falsifiers while v1 remains the
   only active runtime contract.
2. In one merge unit, move `REQ-CI-RUNTIME-030` from deferred to blocking and
   make the successor receipt the only code-level shape admissible for future
   production activation. No intermediate state may claim that v1 implements
   the successor predicate.
3. Bind baseline, delta, expected relation, producer, workflow-manifest,
   source-binding provenance, provider, policy, catalog, registry, and
   production-subject epochs without treating a source commit as the stable
   workflow-authority identity.
4. Reject old, missing, stale, cross-scope, or unverifiable relation evidence
   in enforcing mode.
5. Add a durable per-scope cutover generation consumed by every selected-plan
   decision. Each selected decision also requires a current source-binding
   receipt for the same stable workflow manifest and a bounded live provider
   observation equal to the receipt's provider-governance digest. Generation
   mismatch, missing or stale source binding, provider timeout, unavailable or
   mismatched provider evidence, old binary, stale plan, or stale lease yields
   FullCI.
6. Before successor activation, latch the existing v1 `disable_omission`
   transactional guard, revoke v1 grants, drain issued v1 plans, leases, and
   their target executions, and prove all old replicas unroutable. Then
   atomically install the successor generation and exact relation receipt and
   clear the latch.
7. Require every authorized provider-governance mutation to latch
   `disable_omission`, drain active selected authority, prove FullCI, install a
   post-change relation receipt and generation, and only then clear the latch.
   Keep non-enforcing mode, out-of-band provider mutation, and request-time
   uncertainty on FullCI.

Exit: no receipt lacking relation closure can mint selected-plan authority, and
no mixed-version runtime can mint or consume selected authority.

### Stage E: Pilot evidence

1. Produce both manifests for the frozen pilot branch.
2. Exercise empty-baseline, agreed-omission, unauthorized retirement or
   introduction, missing-row, extra-row, same-ID/different-value, stale-epoch,
   correlated-enumeration, unclassified raw candidate, unknown-state,
   unknown-object-type, symlink, gitlink, incomplete-traversal, tree-object or
   commit-chain mismatch, manifest-nullability, unchanged-workflows/new-commit,
   missing current source binding, provider-digest mismatch, and provider-read
   timeout falsifiers locally.
3. Run one shadow provider canary while native FullCI remains required.
4. Record wall time, runner occupancy, selected jobs, fallback reason, and
   unsafe omission count against the frozen baseline.

Exit: zero unsafe omissions in the owner-approved observation window and an
independently reviewable relation receipt. This still does not authorize
cutover without the remaining production-admission conjuncts.

## 4. Ownership

| Responsibility                                | Owner                                               |
|-----------------------------------------------|-----------------------------------------------------|
| Phase-0 baseline and transition authorization | target repository and platform owners               |
| Row algebra and exact comparison              | planned pure `target_authority_relation` capability |
| Owner-intended registration projection        | target-artifact and policy owners                   |
| Exact-revision workflow observation           | `workflow_discovery` and GitHub adapter             |
| Provider gate observation                     | governance/provider evidence capability             |
| Receipt admission and opaque authority        | `production_admission`                              |
| Scope generation and replica drain            | runtime and deployment owners                       |
| Request-time immutable snapshot verification  | existing revision-bound adapter admission           |

The later `release_evidence` projection may consume the relation receipt, but
it does not own or redefine relation rows. This prevents release-manifest
composition from becoming a second comparison authority.

The application layer may orchestrate these capabilities but may not redefine
row semantics or provider evidence.

## 5. Required Witnesses

- codec/schema differential tests;
- Phase-0 baseline and transition-conservation property tests;
- property and mutation tests for every row component;
- producer-independence negative tests;
- complete key-set and full-row comparison tests;
- stale and mixed-epoch tests;
- raw-domain mutation tests proving every workflow and provider candidate has
  exactly one authority or owner-approved non-authority projection;
- mixed-version, revoked-v1-grant, stale-plan, stale-lease, non-terminal old
  execution, and failed-drain cutover tests;
- add, remove, modify, retype, and mode-change tests for a workflow outside the
  registry adapter, plus unknown type, symlink, gitlink, incomplete traversal,
  tree-object mismatch, commit/root/subtree-chain mismatch, nullable-field
  mismatch, provider/local size mismatch, and a non-CI commit with an unchanged
  workflow manifest;
- production-admission old-receipt and missing-evidence rejection tests;
- exact target artifact and consumer-contract laboratory checks;
- requirements, Proofkit routing, import-boundary, documentation graph, lint,
  type, unit, integration, and branch-head quality gates; and
- a live provider canary that retains native FullCI.

## 6. Rollback

Before enforcement, rollback removes the unaccepted relation producer and
keeps FullCI. After admission support exists, disabling relation issuance or
removing its receipt causes enforcing startup rejection; non-enforcing mode
continues to issue only FullCI. No rollback path may accept the previous receipt
shape for selected execution.

## 7. Non-Claims

This plan does not claim implementation, signer deployment, provider
configuration, production authority, performance savings, or a five-minute
worst-case workflow. Performance claims require frozen provider measurements;
safety claims remain bounded to the listed witnesses and epochs.
