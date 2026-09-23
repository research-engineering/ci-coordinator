# Compact Proof Route Authority Implementation Plan

Status: implementation plan

Owner: `ci-coordinator.proofkit-adoption`

Design authority:
[Compact Proof Route Authority](compact-proof-route-authority.md)

## 1. Objective

Replace the expanded route graph with a bounded owner-partitioned relation while
preserving every operative requirement field, command, route, tuple, and
selective-plan observable. Retire binding-local requirement `nonClaims` in favor
of the canonical requirement-source text and report the exact changed count.

## 2. Ordered Work

1. Freeze the base requirement, command, route, path-command, and required-tuple
   relations.
2. Benchmark candidate representations by bytes, lexical repetition, review
   locality, and additional admission complexity; reject LOC-only improvements.
3. Bind this design, the index, owner sources, adapters, and falsifiers to the
   existing route-closure requirement.
4. Materialize one v2 source per requirement package and replace the stable
   binding path atomically with its sorted SHA-256 index.
5. Add a pure projector and a bounded current/historical loader. Neither may
   execute witnesses or own native Proofkit semantics.
6. Route requirements admission, selective planning, plan checking, retirement
   checks, and mutation manifests through the normalized projection.
7. Submit the complete projection to native Proofkit binding admission.
8. Keep required tuples independently authored; compact only their JSON
   serialization.
9. Add falsifiers for hash drift, source closure, identity collisions, unknown
   commands, environment drift, route deletion, undeclared additions, and tuple
   loss.
10. Run repository-owned local static gates, publish one PR, require exact-head
    Full Check and CodeQL, then squash-merge.

## 3. File-Level Change Map

| Surface                                    | Change                            | Acceptance                              |
|--------------------------------------------|-----------------------------------|-----------------------------------------|
| `proofkit/requirement-bindings.json`       | Sorted v2 source index            | Exact hashes and bounded inventory      |
| `proofkit/routes/*.v2.json`                | Owner route rows with command IDs | Reversible route relation               |
| `proofkit/required-binding-tuples.v1.json` | Compact serialization             | Decoded owner relation preserved        |
| `scripts/proofkit_route_contract.py`       | Pure projection and parity        | Differential falsifiers pass            |
| `scripts/proofkit_route_sources.py`        | Bounded source loading            | Hash and source-closure falsifiers pass |
| `scripts/proofkit_requirements.py`         | Normalize, compare epochs, admit  | Native admission and tuple closure pass |
| `scripts/proofkit_selective_plan.py`       | Consume exact-epoch projection    | Existing routing semantics pass         |
| `scripts/proofkit_plan_check.py`           | Reuse loaded projections          | Complete routing corpus passes          |
| Proofkit tests and mutation manifests      | Cover new failure surface         | No admitted mutant survives             |
| Documentation and profile routing          | Register nested route sources     | Graph and proof-like closure pass       |

## 4. Review Checkpoints

Checkpoint A admits design and measured alternatives. Checkpoint B proves the
complete projection against the frozen base relation. Checkpoint C independently
reviews the final diff, resource bounds, and failure semantics. Any material
change invalidates later receipts.

## 5. Completion Predicate

```text
Complete iff
  DesignLawsImplemented
  and BaseRoutesPreserved
  and BaseRequiredTuplesPreserved
  and RequirementNonClaimsOwnedByCanonicalSources
  and AdditionsBelongOnlyToAddedPaths
  and RequiredTupleClosure
  and NativeProofkitAdmissionPassed
  and NegativeCorpusPassed
  and NoTrackedExpandedProjection
  and ExactHeadProviderChecksPassed
```

Completion does not imply native witness adequacy or production readiness.
