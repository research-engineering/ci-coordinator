# Planning Core Module Specification

Status: module specification

Date: 2026-07-13

## 1. Purpose And Boundary

`planning_core` is the pure deterministic mapping from one already-admitted
`PlanningInput` and one already-typed `PlanningPolicy` to a candidate coverage
plan. It owns impact closure, obligation selection, witness closure, FullCI
fallback, and the proof object for each omitted obligation.

It does not parse repository configuration, call GitHub, read a clock or an
environment, admit agent output, verify a supplied plan, issue a signature,
persist a plan, calculate runner capacity, shard execution, or dispatch work.

The boundary follows from the following separation law:

```text
Coverage(P, I) is a pure function of admitted policy P and context I.
Admission(P), Verification(plan), Advice(raw), Capacity(snapshot), and Dispatch(plan)
have independent failure algebras.
Therefore none of those owners may be hidden inside Coverage(P, I).
```

## 2. Public Contract

```text
config_control.planning_projection.project_dynamic_ci_planning(
  draft: ValidatedEpochDraft
) -> DynamicCiPlanningProjection
PlanningPolicy.from_projection(projection: DynamicCiPlanningProjection) -> PlanningPolicy
PolicySnapshot.from_projection(projection: DynamicCiPlanningProjection) -> PolicySnapshot
plan(input: PlanningInput, policy: PlanningPolicy) -> DeterministicPlan | PlanningRejected
analyze_impact(input: PlanningInput) -> ImpactAnalysis
build_omission_proof(input, policy, obligation, impact) -> OmissionProof | NotOmittable
```

`PlanningPolicy` is an immutable, typed projection of an already compiled
configuration. It contains only planner facts: policy identities, a closed
`ValidationCatalog`, bounded fallback timeout, advice-admission parameters, and
responsibility surfaces. Config parsing and normalization remain
`config_control` authority. Provider workflow inventory and static-job
executability belong to the later execution-admission boundary.

`config_control.planning_projection` owns `project_dynamic_ci_planning`. Its
production factory accepts only a `ValidatedEpochDraft`, recomputes the
compiled-policy hash and epoch identity, then binds its typed projection to the
compiled dynamic-CI fields. Its `assert_integrity()` operation re-derives every
exposed fact from the retained admitted epoch. `PlanningPolicy` and
`PolicySnapshot` invoke that operation before construction. Consequently,
changing catalog entities, dependency-graph source, or global-risk paths while copying an
earlier epoch identity is rejected before either planning value is constructed.
This module-local public API does not enlarge the frozen policy-admission
package-root ABI.

`DeterministicPlan` is an internal candidate, not a verified, signed, or
provider-enforced plan. Its identity binds the planner schema and all context
identities used for coverage.

## 3. Admission And Fallback Algebra

Let `I` be a `PlanningInput`, `P` a `PlanningPolicy`, and `O(P)` its obligations.

```text
PlannerAdmitted(I, P) iff
  not I.full_ci_invalidating
  and P.config_epoch_id = I.policy.epoch_id
  and P.compiled_policy_hash = I.policy.compiled_policy_hash
  and P.policy_hash = I.policy.policy_hash
```

```text
PolicyIdentityMismatch(I, P) => PlanningRejected
not PolicyIdentityMismatch(I, P) and not PlannerAdmitted(I, P) => FullCI(I, P)
FullCI(I, P) selects every o in O(P) at o.full_depth
```

`PlanningPolicy` is already an admitted typed projection. A malformed raw
policy therefore cannot enter this module: its rejection belongs to
`config_control` or runtime composition. The only policy failures handled here
are inconsistencies between an otherwise well-formed projection and the
admitted context. A policy-identity inconsistency returns `PlanningRejected`;
the runtime composition owner, which holds the active epoch, must trigger its
provider fallback. A context uncertainty with matching policy identity produces
FullCI. The fallback timeout is a policy value only after typed validation.

This is sufficient for fail-closed selection: if any predicate needed to prove
an omission is absent, false, or inconsistent, no omission is emitted. A
constructor error is not silently converted into a reduced plan.

## 4. Impact And Omission Laws

`Impact(I)` starts with all current and previous changed paths, closes over
graph dependents, and accumulates reachable risk classes. All sets use the
kernel-owned ECMAScript UTF-16 ordering primitive.

For an obligation `o`, define:

```text
CanOmit(I, P, o) iff
  PlannerAdmitted(I, P)
  and o.omit_allowed
  and GlobalRiskChanged(I) = false
  and PathAffected(o, I) = false
  and ResponsibilityRisks(o) intersects ImpactRisks(I) = empty
```

```text
PathAffected(o, I) iff
  exists pattern in ResponsibilityPaths(o), path in ImpactPaths(I):
    matches_path_pattern(pattern, path)
```

```text
CanOmit(I, P, o) => exactly one OmissionProof(o)
not CanOmit(I, P, o) => o is selected at o.default_depth
```

An omission proof contains the obligation identity, predicate truths,
assumptions, invalidators, and hashes for repository epoch, diff, policy,
dependency graph, and configured validation catalog. A selected obligation
carries its nominal identity, depth, and required witness identities. Witness
closure chooses the strongest requested depth for every required witness.

## 5. Determinism And Monotonicity

```text
SameCanonicalInput => SameDeterministicPlan
Permutation(diff files, graph nodes, catalog entities) => SameDeterministicPlan
UnknownOrTruncatedOrStaleInput => FullCI
CapacityFacts do not change coverage selection
AdviceFacts do not change coverage selection
```

For a fixed admitted input and policy, no output choice depends on ambient
state. Any future advice or capacity transform must consume the plan through
its own owner and may not weaken its selected coverage.

## 6. Failure Taxonomy

| Condition                                    | Required result                        | Owner of later recovery      |
|----------------------------------------------|----------------------------------------|------------------------------|
| invalid diff, graph, or input identity       | FullCI candidate                       | verification and execution   |
| policy identity mismatch                     | typed `PlanningRejected`, no candidate | runtime composition          |
| context incompatibility with matching policy | FullCI candidate                       | config/repository refresh    |
| malformed raw policy                         | rejected before this boundary          | config admission/composition |
| unknown changed path                         | FullCI candidate                       | repository-context refresh   |
| policy prevents omission                     | selected obligation at default depth   | none                         |
| responsibility intersects impact             | selected obligation at default depth   | none                         |

The planner returns a candidate for every valid value object. It does not throw
for a recoverable coverage uncertainty. Construction-time type violations remain
local programmer errors and are rejected at the typed boundary.

## 7. Ownership And Decomposition

```text
planning_core/model.py          immutable public value types and plan identity
planning_core/policy.py         planner-policy validation and identity admission
planning_core/impact.py         transitive closure and impact facts
planning_core/omission_proof.py omission proof construction
planning_core/planner.py        pure orchestration and fallback selection
```

No file owns a second bounded context. `planner.py` coordinates the above
owners and must not import FastAPI, SQLAlchemy, GitHub adapters, environment
settings, clocks, agent clients, verifier, issuance, capacity, or execution.

## 8. Proof Obligations

| Invariant                        | Smallest falsifier                                      | Required evidence                |
|----------------------------------|---------------------------------------------------------|----------------------------------|
| unknown input cannot omit        | unknown path omits a check                              | unit and conformance case        |
| fallback is total                | invalid policy crashes or reduces coverage              | unit matrix                      |
| closure is deterministic         | graph ordering changes the output                       | permutation test                 |
| omission proof is complete       | missing hash or predicate is emitted                    | structural test                  |
| planner has no ambient authority | forbidden import changes coverage                       | import-boundary witness          |
| migrated overlap is not weaker   | partition/depth/identity differs from required relation | rules-pinned conformance witness |

## 9. Non-Claims

This module does not approve production omission, verify a signed plan, admit
agent advice, or prove live GitHub Actions behavior.
